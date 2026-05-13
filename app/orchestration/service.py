from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from langchain_google_genai import ChatGoogleGenerativeAI

from app.core.assistants import AssistantConfig
from app.core.config import settings
from app.llms.gemini import resolve_gemini_model_name
from app.orchestration.agents import (
    CourtClassifier,
    IntentClassifier,
    MilvusQueryAgent,
    evaluate_context_sufficiency,
    merge_web_results,
    run_web_search_fallback,
    should_run_web_search,
)
from app.orchestration.prompts import PromptRegistry
from app.orchestration.state import GraphContext, RetrievalRewriteState


@dataclass
class WebSearchAgent:
    evaluate_context_sufficiency = staticmethod(evaluate_context_sufficiency)
    run_web_search_fallback = staticmethod(run_web_search_fallback)
    merge_web_results = staticmethod(merge_web_results)
    should_run_web_search = staticmethod(should_run_web_search)


class OrchestrationService:
    """Loads external systems and exposes the compiled retrieval graph."""

    def __init__(self) -> None:
        self.context = GraphContext(user_id="")
        self.prompt_registry = PromptRegistry()
        self.intent_classifier = IntentClassifier(self.prompt_registry)
        self.court_classifier = CourtClassifier(self.prompt_registry)
        self.milvus_agent = MilvusQueryAgent(self.prompt_registry)
        self.web_search = WebSearchAgent()
        self._checkpointer: Any | None = None
        self._store: Any | None = None
        self._graph: Any | None = None
        self._rewrite_llm: Any | None = None
        self._generation_llm: Any | None = None

    @property
    def checkpointer(self) -> Any:
        if self._checkpointer is None:
            self._checkpointer = self._build_checkpointer()
        return self._checkpointer

    @property
    def store(self) -> Any:
        if self._store is None:
            self._store = self._build_store()
        return self._store

    @property
    def rewrite_llm(self) -> Any:
        if self._rewrite_llm is None:
            self._rewrite_llm = self._build_llm()
        return self._rewrite_llm

    @property
    def generation_llm(self) -> Any:
        if self._generation_llm is None:
            self._generation_llm = self._build_llm()
        return self._generation_llm

    def graph(self) -> Any:
        if self._graph is None:
            from app.orchestration.graph import compile_retrieval_graph

            self._graph = compile_retrieval_graph(self)
        return self._graph

    def web_search_enabled(self, state: RetrievalRewriteState) -> bool:
        assistant = state.get("assistant_name") or state.get("collection_name")
        if state.get("deep_research"):
            return bool(settings.TAVILY_API_KEY)
        return AssistantConfig.is_web_search_enabled(assistant)

    @staticmethod
    def build_payload(
        *,
        query: str,
        user_id: str,
        session_id: str,
        message_id: str,
        assistant_name: str,
        file_ids: list[str] | None = None,
        project_id: str | None = None,
        file_context: str | None = None,
    ) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "query": query,
            "user_id": user_id,
            "session_id": session_id,
            "message_id": message_id,
            "assistant_name": assistant_name,
            "deep_research": AssistantConfig.is_deep_research_assistant(
                assistant_name
            ),
            "file_ids": file_ids,
            "project_id": project_id,
        }
        if file_context is not None and str(file_context).strip():
            payload["file_context"] = str(file_context).strip()
        return payload

    async def arun(self, payload: dict[str, Any]) -> dict[str, Any]:
        user_id = payload["user_id"]
        session_id = payload["session_id"]
        self.context = GraphContext(user_id=user_id)
        thread_id = f"{user_id}:{session_id}"
        graph = self.graph()

        result = await graph.ainvoke(
            payload,
            config={"configurable": {"thread_id": thread_id}},
            context=self.context,
        )

        return {
            "original_query": payload["query"],
            "rewritten_query": result.get("rewritten_query"),
            "retrieval_context": result.get("retrieval_context", ""),
            "final_answer": result.get("final_answer"),
            "result": result,
        }

    def _build_llm(self) -> ChatGoogleGenerativeAI:
        if not settings.GEMINI_API_KEY:
            raise RuntimeError("GEMINI_API_KEY is required for orchestration LLM calls.")

        model = resolve_gemini_model_name(
            settings.GEMINI_LANGCHAIN_CHAT_MODEL or settings.DEFAULT_CHAT_MODEL or "gemini-2.5-flash"
        )
        return ChatGoogleGenerativeAI(
            model=model,
            google_api_key=settings.GEMINI_API_KEY,
            temperature=settings.TEMPERATURE,
        )

    def _build_checkpointer(self) -> Any:
        """Use the same AsyncRedisSaver as ``init_agent_checkpointer`` (app lifespan).

        LangGraph ``ainvoke`` / ``astream`` call ``await checkpointer.aget_tuple(...)``.
        Sync ``langgraph.checkpoint.redis.RedisSaver`` does not implement async
        checkpoint APIs, so the base class raises a bare ``NotImplementedError``.
        """
        from app.orchestration.utils import _get_agent_checkpointer

        return _get_agent_checkpointer()

    def _build_store(self) -> Any:
        from langgraph.store.mongodb import MongoDBStore

        if not settings.MONGODB_URI:
            raise RuntimeError("MONGODB_URI is required for orchestration long-term memory.")

        store_cm = MongoDBStore.from_conn_string(
            conn_string=settings.MONGODB_URI,
            db_name=settings.MONGODB_DB_NAME,
            collection_name="long_term_memory",
        )
        store = store_cm.__enter__()
        self._store_cm = store_cm
        return store
