from __future__ import annotations

from collections.abc import AsyncGenerator
from enum import Enum
from typing import Any

from langchain_core.messages import AIMessage
from langchain_google_genai import ChatGoogleGenerativeAI

from app.core.assistants import AssistantConfig
from app.core.config import settings
from app.core.logger import logger
from app.orchestration.agents import CourtClassifier, IntentClassifier, MilvusQueryAgent
from app.orchestration.agents import web_search_fallback as web_search_agent
from app.orchestration.providers import resolve_gemini_model_name
from app.orchestration.prompts import PromptRegistry
from app.orchestration.state import GraphContext, RetrievalRewriteState


class Purpose(Enum):
    GENERATION = "generation"
    LITE = "lite"


class OrchestrationService:
    """Loads external systems and exposes the compiled retrieval graph."""

    def __init__(self) -> None:
        self.context = GraphContext(user_id="")
        self.prompt_registry = PromptRegistry()
        self.intent_classifier = IntentClassifier(self.prompt_registry)
        self.court_classifier = CourtClassifier(self.prompt_registry)
        self.milvus_agent = MilvusQueryAgent(self.prompt_registry)
        self.web_search = web_search_agent
        self._checkpointer: Any | None = None
        self._store: Any | None = None
        self._graph: Any | None = None
        self._lite_llm: Any | None = None
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
    def lite_llm(self) -> Any:
        if self._lite_llm is None:
            self._lite_llm = self._build_llm(settings.DEFAULT_LITE_MODEL, Purpose.LITE)
        return self._lite_llm

    @property
    def generation_llm(self) -> Any:
        if self._generation_llm is None:
            self._generation_llm = self._build_llm(
                settings.DEFAULT_CHAT_MODEL,
                Purpose.GENERATION,
            )
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
        result = await self.prepare_final_state(payload)
        answer_chunks: list[str] = []
        async for item in self.astream_final_answer(result):
            if isinstance(item, str):
                answer_chunks.append(item)
        answer = "".join(answer_chunks).strip()
        result["final_answer"] = answer
        result["messages"] = [AIMessage(content=answer)]
        result["retrieval_context"] = ""

        return {
            "original_query": payload["query"],
            "rewritten_query": result.get("rewritten_query"),
            "retrieval_context": result.get("retrieval_context", ""),
            "final_answer": answer,
            "result": result,
        }

    async def prepare_final_state(self, payload: dict[str, Any]) -> dict[str, Any]:
        from app.orchestration import nodes

        self.context = GraphContext(user_id=payload["user_id"])
        state: dict[str, Any] = dict(payload)

        async def apply(update: dict[str, Any] | None) -> None:
            if update:
                state.update(update)

        await apply(await nodes.load_file_and_project_context(state, self))
        await apply(nodes.ingest_payload(state))
        await apply(nodes.load_long_term_memory(state, self, self.store))
        await apply(await nodes.recognize_intent(state, self))
        await apply(await nodes.route_court(state, self))
        await apply(await nodes.rewrite_query(state, self))
        await apply(await nodes.retrieve_documents(state, self))

        if self.web_search_enabled(state):
            await apply(await nodes.evaluate_context(state, self))
            await apply(await nodes.web_search_fallback(state, self))

        return state

    async def astream_final_answer(
        self,
        state: dict[str, Any],
    ) -> AsyncGenerator[str | dict[str, str], None]:
        from app.orchestration import nodes

        async for item in nodes.stream_final_answer(state, self):
            yield item

    def _build_llm(self, model_name: str, purpose: Purpose) -> ChatGoogleGenerativeAI:
        if not settings.GEMINI_API_KEY:
            raise RuntimeError(
                "GEMINI_API_KEY is required for orchestration LLM calls."
            )

        model_name = resolve_gemini_model_name(model_name)
        logger.info(f"Gemini/Google Generative AI model: {model_name} for {purpose}")

        additional_kwargs: dict[str, Any] = {}
        if purpose == Purpose.GENERATION:
            additional_kwargs = {
                "max_output_tokens": settings.OUTPUT_MAX_TOKENS,
                "temperature": settings.TEMPERATURE,
                "thinking_level": settings.GEMINI_LANGCHAIN_THINKING_LEVEL,
                "include_thoughts": True,
                "streaming": True,
            }
        elif purpose == Purpose.LITE:
            additional_kwargs = {
                "max_output_tokens": 512,  # less tokens for rewrite
                "temperature": 0.0,  # deterministic as possible
                "streaming": False,
            }

        return ChatGoogleGenerativeAI(
            model=model_name,
            google_api_key=settings.GEMINI_API_KEY,
            **additional_kwargs,
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
