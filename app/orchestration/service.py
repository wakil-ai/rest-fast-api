from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

from langchain_core.messages import BaseMessage
from langchain_google_genai import ChatGoogleGenerativeAI

from app.core.assistants import AssistantConfig
from app.core.config import settings
from app.core.logger import logger
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

_MAX_DEBUG_STR = 500
_MAX_DEBUG_MESSAGES = 12


def _summary_for_thread_state_debug(values: dict[str, Any]) -> dict[str, Any]:
    """Shrink orchestration state for a single debug log line."""
    out: dict[str, Any] = {}
    for key, raw in values.items():
        if key == "messages":
            rows: list[dict[str, Any]] = []
            for msg in (raw or [])[-_MAX_DEBUG_MESSAGES:]:
                if isinstance(msg, BaseMessage):
                    body = msg.content
                    if isinstance(body, str) and len(body) > _MAX_DEBUG_STR:
                        body = body[:_MAX_DEBUG_STR] + "…"
                    rows.append(
                        {
                            "type": getattr(msg, "type", type(msg).__name__),
                            "content": body,
                        }
                    )
                else:
                    rows.append({"repr": repr(msg)[:200]})
            out[key] = rows
            continue
        if key in (
            "file_context",
            "project_related_context",
            "final_answer",
        ):
            s = raw if isinstance(raw, str) else ("" if raw is None else str(raw))
            out[key] = (
                f"<{len(s)} chars> {s[:_MAX_DEBUG_STR]}"
                if len(s) > _MAX_DEBUG_STR
                else s
            )
            continue
        if key == "retrieval_context":
            # Omitted from logs: large and cleared from checkpoint after answer anyway.
            continue
        if key == "answer_prompt_template" and raw is not None:
            out[key] = f"<{type(raw).__name__}>"
            continue
        if isinstance(raw, (str, int, float, bool)) or raw is None:
            if isinstance(raw, str) and len(raw) > _MAX_DEBUG_STR:
                out[key] = raw[:_MAX_DEBUG_STR] + "…"
            else:
                out[key] = raw
        elif isinstance(raw, (list, dict)):
            try:
                text = json.dumps(raw, default=str)
                out[key] = text[:800] + ("…" if len(text) > 800 else "")
            except TypeError:
                out[key] = repr(raw)[:400]
        else:
            out[key] = repr(raw)[:400]
    return out


async def _log_orchestration_thread_state_debug(
    *,
    graph: Any,
    thread_id: str,
    message_id: str,
    info: bool,
) -> None:
    try:
        config = {"configurable": {"thread_id": thread_id}}
        snap = await graph.aget_state(config)
        values = snap.values if isinstance(snap.values, dict) else {}
        summary = _summary_for_thread_state_debug(values)
        state_json = json.dumps(summary, default=str, ensure_ascii=False)
        next_nodes = list(getattr(snap, "next", []) or [])
        created_at = getattr(snap, "created_at", None)
        log_fn = logger.info if info else logger.debug
        log_fn(
            f"[orchestration] thread_state thread_id={thread_id} message_id={message_id} "
            f"next={next_nodes} created_at={created_at} state={state_json}"
        )
    except Exception as exc:
        log_fn = logger.info if info else logger.debug
        log_fn(
            f"[orchestration] thread_state snapshot skipped thread_id={thread_id}: {exc}"
        )


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

        if settings.ORCHESTRATION_DEBUG_THREAD_STATE:
            await _log_orchestration_thread_state_debug(
                graph=graph,
                thread_id=thread_id,
                message_id=str(payload.get("message_id") or ""),
                info=True,
            )
        elif settings.DEBUG:
            await _log_orchestration_thread_state_debug(
                graph=graph,
                thread_id=thread_id,
                message_id=str(payload.get("message_id") or ""),
                info=False,
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
