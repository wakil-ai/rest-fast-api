"""Shared orchestration helpers: agent state, checkpoints, uploads, and web search."""

from __future__ import annotations

import asyncio
import json
import warnings
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import TYPE_CHECKING, Any

from langchain_community.tools.tavily_search import TavilySearchResults
from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, SystemMessage, ToolMessage
from langchain_core.messages import messages_from_dict
from pydantic import BaseModel, Field
from tavily import TavilyClient

from app.core.assistants import AssistantConfig
from app.core.config import settings
from app.core.dependencies import (
    get_chat_history_service,
    get_embedding_manager,
    get_orchestration_service,
    get_prompt_registry,
    get_retrieval_service,
)
from app.core.logger import logger
from app.orchestration.llms import LangChain
from app.orchestration.text import message_content_to_plain_str
from app.utils.tokens import count_tokens, truncate_to_token_limit

if TYPE_CHECKING:
    from app.assistants.base import BaseAgent
    from app.orchestration.state import RetrievalRewriteState

_USER_FILE_CONTEXT_PREFIX = (
    "# USER FILE CONTEXT:\n"
    "Note: This is the context of the user uploaded files."
)

_agent_checkpointer: Any | None = None
_checkpointer_needs_async_close = False


@dataclass
class AgentRequestContext:
    """Inputs for one assistant-agent chat turn."""

    user_id: str
    session_id: str
    message_id: str
    query: str
    assistant: str
    file_ids: list[str] | None = None
    project_id: str | None = None
    stream: bool = False
    preloaded_file_context: str | None = None


@dataclass
class AgentState:
    """Mutable state owned by an assistant agent while it handles a turn."""

    request: AgentRequestContext
    resolved_assistant: str
    chat_history: str = ""
    memory_context: str = ""
    file_context: str = ""
    retrieval_context: str = ""
    system_prompt: str = ""
    answer: str = ""
    attachments: list[dict[str, Any]] | None = None
    metadata: dict[str, Any] | None = None
    classified_legal_intent: str | None = None
    court_route_tag: str | None = None
    error: str | None = None


@dataclass
class GenerationContext:
    """All context needed for one chat-agent generation."""

    user_id: str
    context: str
    system_prompt: str
    chat_history: str
    assistant_name: str
    attachments: list[dict[str, Any]] | None = None
    classified_legal_intent: str | None = None
    court_route_tag: str | None = None
    langgraph_thread_id: str | None = None


@dataclass
class AgentRunResult:
    """Final non-streaming result from an assistant agent."""

    answer: str
    resolved_assistant: str
    metadata: dict[str, Any]
    attachments: list[dict[str, Any]] | None = None
    generation_context: GenerationContext | None = None


class ContextEvaluationResponse(BaseModel):
    is_sufficient: bool = Field(
        description="Whether the context is sufficient to answer the query"
    )
    reasoning: str = Field(description="Explanation for the sufficiency decision")
    missing_info: str | None = Field(
        default="", description="Description of missing information if insufficient"
    )


class WebSearchDocument(BaseModel):
    url: str = Field(description="Source URL of the document")
    title: str | None = Field(default=None, description="Document title")
    content: str = Field(description="Extracted and summarized content")


class WebSearchResponse(BaseModel):
    docs: list[WebSearchDocument] = Field(
        default_factory=list,
        description="List of extracted and summarized web documents",
    )


class LangGraphRedisCheckpointerError(RuntimeError):
    """Raised when LangGraph AsyncRedisSaver is required but unavailable."""


def build_web_search_tool() -> Any | None:
    if not settings.TAVILY_API_KEY:
        logger.warning("TAVILY_API_KEY is not configured; skipping web search tool")
        return None
    return TavilySearchResults(api_key=settings.TAVILY_API_KEY, max_results=5)


def agent_session_thread_id(user_id: str, session_id: str) -> str:
    return f"{user_id}:{session_id}"


def agent_turn_thread_id(
    user_id: str, session_id: str, assistant_name: str, message_id: str
) -> str:
    safe_asst = AssistantConfig.validate_assistant_or_default(assistant_name)
    return f"{user_id}:{session_id}:{safe_asst}:{message_id}"


def _require_langgraph_redis_enabled() -> None:
    if not settings.LANGGRAPH_CHECKPOINT_USE_REDIS:
        raise LangGraphRedisCheckpointerError(
            "LangGraph checkpoints require AsyncRedisSaver. "
            "Set LANGGRAPH_CHECKPOINT_USE_REDIS=true and configure REDIS_URI "
            "or REDIS_HOST/REDIS_PORT."
        )


def _build_langgraph_redis_url() -> str:
    uri = settings.REDIS_URI
    if isinstance(uri, str) and uri.strip():
        return uri.strip()
    password = settings.REDIS_PASSWORD
    if isinstance(password, str) and password:
        return f"redis://:{password}@{settings.REDIS_HOST}:{settings.REDIS_PORT}/0"
    return f"redis://{settings.REDIS_HOST}:{settings.REDIS_PORT}/0"


def _default_ttl_minutes_for_checkpoints() -> float:
    secs = max(int(settings.LANGGRAPH_CHECKPOINT_TTL_SECONDS), 60)
    return float(secs) / 60.0


async def delete_agent_thread(*, user_id: str, session_id: str) -> None:
    thread_id = agent_session_thread_id(user_id, session_id)
    try:
        cp = _get_agent_checkpointer()
        adelete = getattr(cp, "adelete_thread", None)
        if adelete is not None:
            await adelete(thread_id)
            logger.info(f"[chat_agent] Deleted LangGraph thread {thread_id}")
            return
        sync_del = getattr(cp, "delete_thread", None)
        if sync_del is not None:
            await asyncio.to_thread(sync_del, thread_id)
            logger.info(f"[chat_agent] Deleted LangGraph thread {thread_id} (sync)")
    except Exception:
        logger.warning(
            f"[chat_agent] Failed to delete LangGraph thread {thread_id}",
            exc_info=True,
        )


async def init_agent_checkpointer() -> None:
    global _agent_checkpointer, _checkpointer_needs_async_close

    _checkpointer_needs_async_close = False
    _agent_checkpointer = None
    _require_langgraph_redis_enabled()

    try:
        from langgraph.checkpoint.redis.aio import AsyncRedisSaver
    except ImportError as exc:
        raise LangGraphRedisCheckpointerError(
            "LangGraph checkpoints require the langgraph-checkpoint-redis package."
        ) from exc

    url = _build_langgraph_redis_url()
    ttl_minutes = _default_ttl_minutes_for_checkpoints()
    with warnings.catch_warnings():
        warnings.filterwarnings(
            "ignore",
            message=r"get_async_redis_connection will become async",
            category=DeprecationWarning,
        )
        saver = AsyncRedisSaver(
            redis_url=url,
            ttl={"default_ttl": ttl_minutes},
            checkpoint_prefix="wakilai:lg:checkpoint",
            checkpoint_write_prefix="wakilai:lg:checkpoint_write",
        )
        await saver.setup()
    _agent_checkpointer = saver
    _checkpointer_needs_async_close = True
    log_url = url.split("@")[-1] if "@" in url else url
    ttl_seconds = int(settings.LANGGRAPH_CHECKPOINT_TTL_SECONDS)
    logger.info(
        f"[chat_agent] LangGraph checkpointer: AsyncRedisSaver ({log_url}, "
        f"ttl≈{ttl_minutes:.0f} min, {ttl_seconds}s)"
    )


async def shutdown_agent_checkpointer() -> None:
    global _agent_checkpointer, _checkpointer_needs_async_close

    cp = _agent_checkpointer
    should_close = _checkpointer_needs_async_close
    _agent_checkpointer = None
    _checkpointer_needs_async_close = False

    if cp is not None and should_close:
        await cp.__aexit__(None, None, None)


def _get_agent_checkpointer() -> Any:
    global _agent_checkpointer
    if _agent_checkpointer is None:
        raise LangGraphRedisCheckpointerError(
            "LangGraph AsyncRedisSaver is not initialized. "
            "Application startup must call init_agent_checkpointer()."
        )
    return _agent_checkpointer


async def load_langgraph_agent_thread_messages(thread_id: str) -> list[BaseMessage]:
    """Load persisted ``messages`` from the LangGraph Redis thread (``create_agent`` / LangChain)."""
    if not str(thread_id or "").strip():
        return []
    cp = _get_agent_checkpointer()
    aget = getattr(cp, "aget_tuple", None)
    if not callable(aget):
        return []
    try:
        tup = await aget({"configurable": {"thread_id": thread_id}})
    except Exception as exc:
        logger.warning(
            "[orchestration] Failed to read LangGraph thread %r: %s",
            thread_id,
            exc,
            exc_info=True,
        )
        return []
    if tup is None:
        return []
    ch = (tup.checkpoint or {}).get("channel_values") or {}
    raw = ch.get("messages")
    if not isinstance(raw, list):
        return []
    out: list[BaseMessage] = []
    for m in raw:
        if isinstance(m, BaseMessage):
            out.append(m)
        elif isinstance(m, dict):
            try:
                out.extend(messages_from_dict([m]))
            except Exception:
                logger.debug(
                    "[orchestration] Skip one non-LC message dict from thread %r",
                    thread_id,
                    exc_info=True,
                )
    return out


def _debug_thread_state_enabled() -> bool:
    return bool(settings.DEBUG or settings.ORCHESTRATION_DEBUG_THREAD_STATE)


async def log_langgraph_thread_state_debug(
    thread_id: str, *, phase: str = "after_answer"
) -> None:
    """Print and log persisted LangGraph ``messages`` for ``thread_id`` (orchestration final agent)."""
    if not _debug_thread_state_enabled():
        return
    msgs = await load_langgraph_agent_thread_messages(thread_id)
    lines = [
        f"[orchestration-debug] LangGraph thread_id={thread_id!r} phase={phase} "
        f"n_messages={len(msgs)}"
    ]
    for i, m in enumerate(msgs):
        raw = getattr(m, "content", None)
        preview = message_content_to_plain_str(raw)
        if len(preview) > 800:
            preview = preview[:800] + "…"
        lines.append(f"  [{i}] {getattr(m, 'type', m.__class__.__name__)}: {preview!r}")
    block = "\n".join(lines)
    print(block, flush=True)
    logger.info(block)


def log_orchestration_pipeline_messages_debug(
    state: dict[str, Any], *, phase: str
) -> None:
    """Print ``state['messages']`` used for rewrite / retrieval (includes merged Redis history)."""
    if not _debug_thread_state_enabled():
        return
    msgs = state.get("messages")
    if not isinstance(msgs, list):
        msgs = []
    lines = [
        f"[orchestration-debug] pipeline state['messages'] phase={phase} n_messages={len(msgs)}"
    ]
    for i, m in enumerate(msgs):
        if not isinstance(m, BaseMessage):
            lines.append(f"  [{i}] (non-BaseMessage): {m!r}")
            continue
        raw = getattr(m, "content", None)
        preview = message_content_to_plain_str(raw)
        if len(preview) > 800:
            preview = preview[:800] + "…"
        lines.append(f"  [{i}] {getattr(m, 'type', m.__class__.__name__)}: {preview!r}")
    block = "\n".join(lines)
    print(block, flush=True)
    logger.info(block)


async def merge_langgraph_thread_into_state_messages(state: dict[str, Any]) -> None:
    """Copy ``messages`` from the LangGraph Redis thread into ``state`` for pre-agent nodes.

    Rewrite / intent / retrieval run outside ``create_agent``; they read ``state['messages']``.
    This hydrates that list from the same ``thread_id`` the final LangChain agent uses.
    """
    user_id = str(state.get("user_id") or "").strip()
    session_id = str(state.get("session_id") or "").strip()
    if not user_id or not session_id:
        return
    tid = agent_session_thread_id(user_id, session_id)
    prior = await load_langgraph_agent_thread_messages(tid)
    if not prior:
        return
    current = state.get("messages") or []
    if not isinstance(current, list) or not current:
        state["messages"] = list(prior)
        return
    state["messages"] = list(prior) + list(current)


def _format_main_system_instructions() -> str:
    tmpl = get_prompt_registry().get_assistant_prompt("main")
    return tmpl.format(
        context="",
        chat_history="Earlier turns are kept in conversation messages for this thread.",
    )


def _compile_lc_agent(*, system_prompt: str) -> Any:
    return LangChain(checkpointer=_get_agent_checkpointer()).compile_chat(
        system_prompt=system_prompt,
    )


def _truncate(s: str, max_len: int) -> str:
    if len(s) <= max_len:
        return s
    return s[: max_len - 3] + "..."


def serialize_graph_message(
    msg: BaseMessage, *, max_content_len: int = 4000
) -> dict[str, Any]:
    row: dict[str, Any] = {
        "type": getattr(msg, "type", msg.__class__.__name__),
        "name": getattr(msg, "name", None),
    }
    content = getattr(msg, "content", None)
    if isinstance(content, str):
        row["content"] = _truncate(content, max_content_len)
    elif isinstance(content, list):
        row["content_blocks"] = len(content)
        row["content_preview"] = _truncate(repr(content), min(max_content_len, 500))
    else:
        row["content"] = repr(content)[:500] if content is not None else None

    if isinstance(msg, AIMessage):
        tc = getattr(msg, "tool_calls", None)
        if tc:
            serialized_calls: list[dict[str, Any]] = []
            for c in tc:
                if isinstance(c, dict):
                    serialized_calls.append(
                        {
                            "name": c.get("name"),
                            "args": _truncate(repr(c.get("args")), 800),
                            "id": c.get("id"),
                        }
                    )
                else:
                    serialized_calls.append({"repr": _truncate(repr(c), 500)})
            row["tool_calls"] = serialized_calls
    if isinstance(msg, ToolMessage):
        row["tool_call_id"] = getattr(msg, "tool_call_id", None)
    return row


def _agents_config_path() -> Path:
    return Path(__file__).resolve().parent / "config" / "agents.yaml"


@lru_cache
def _agents_yaml() -> dict[str, Any]:
    path = _agents_config_path()
    if not path.exists():
        return {"agents": {}}
    import yaml

    with path.open(encoding="utf-8") as f:
        data = yaml.safe_load(f) or {}
    return data.get("agents", {})


def _agent_instructions(name: str) -> str:
    agents = _agents_yaml()
    block = agents.get(name) or {}
    role = block.get("role", "")
    goal = block.get("goal", "")
    back = block.get("backstory", "")
    return f"Role: {role}\n\nInstructions:\n{goal}\n\nBackground:\n{back}".strip()


def _strip_json_fenced_block(text: str) -> str:
    stripped = text.strip()
    if not stripped.startswith("```"):
        return stripped
    lines = stripped.split("\n")
    if lines and lines[0].startswith("```"):
        lines = lines[1:]
    if lines and lines[-1].strip() == "```":
        lines = lines[:-1]
    return "\n".join(lines).strip()


async def _lite_llm_json_object(system: str, user: str) -> dict[str, Any]:
    """Structured JSON via orchestration lite LLM (same as Purpose.LITE / DEFAULT_LITE_MODEL)."""
    try:
        llm = get_orchestration_service().lite_llm
        msg = await llm.ainvoke(
            [SystemMessage(content=system), HumanMessage(content=user)]
        )
        raw = message_content_to_plain_str(getattr(msg, "content", None))
    except Exception as e:
        logger.warning(f"[retrieval_chain] Lite LLM JSON call failed: {e}", exc_info=True)
        return {}
    if not raw:
        return {}
    payload = _strip_json_fenced_block(raw)
    try:
        return json.loads(payload.replace(": None", ": null"))
    except json.JSONDecodeError:
        logger.warning(f"[retrieval_chain] Invalid JSON from lite model: {raw[:300]}...")
        return {}


_MAX_EVAL_CONTEXT = int(settings.RETRIEVAL_CONTEXT_TOKEN_LIMIT)


async def context_evaluation_llm(query: str, context: str) -> ContextEvaluationResponse:
    spec = _agent_instructions("context_evaluator")
    ctx = context if len(context) <= _MAX_EVAL_CONTEXT else context[:_MAX_EVAL_CONTEXT]
    system = (
        f"{spec}\n\nRespond with one JSON object only. Keys: "
        '"is_sufficient" (boolean), "reasoning" (string), "missing_info" (string, may be empty). '
        "No markdown."
    )
    user = f"QUERY:\n{query}\n\nRETRIEVED_CONTEXT:\n{ctx}"
    data = await _lite_llm_json_object(system, user)
    if not data:
        return ContextEvaluationResponse(
            is_sufficient=True,
            reasoning="Evaluator unavailable; proceeding with corpus context.",
            missing_info="",
        )
    return ContextEvaluationResponse(
        is_sufficient=bool(data.get("is_sufficient", True)),
        reasoning=str(data.get("reasoning") or ""),
        missing_info=str(data.get("missing_info") or ""),
    )


async def web_search_tavily(query: str) -> WebSearchResponse:
    if not settings.TAVILY_API_KEY:
        logger.info("[retrieval_chain] TAVILY_API_KEY unset; skipping web search")
        return WebSearchResponse(docs=[])

    try:
        client = TavilyClient(api_key=settings.TAVILY_API_KEY)
        raw = client.search(
            query,
            max_results=5,
            search_depth="advanced",
            include_answer=True,
        )
    except Exception as e:
        logger.warning(f"[retrieval_chain] Tavily search failed: {e}", exc_info=True)
        return WebSearchResponse(docs=[])

    if isinstance(raw, dict):
        results = raw.get("results")
    else:
        results = getattr(raw, "results", None)
    if not isinstance(results, list):
        return WebSearchResponse(docs=[])

    docs: list[WebSearchDocument] = []
    for item in results[:5]:
        if not isinstance(item, dict):
            continue
        url = str(item.get("url") or "").strip()
        if not url:
            continue
        title = item.get("title")
        content = str(
            item.get("content") or item.get("snippet") or item.get("raw_content") or ""
        ).strip()
        docs.append(
            WebSearchDocument(
                url=url,
                title=str(title) if title else None,
                content=content or "(no extract returned)",
            )
        )
    return WebSearchResponse(docs=docs)


def limit_file_context_for_llm(file_context: str) -> str:
    if not file_context:
        return file_context
    token_count = count_tokens(file_context)
    if token_count <= settings.FILE_CONTENT_TOKEN_LIMIT:
        return file_context
    logger.warning(
        "File context exceeds LLM token limit "
        f"({token_count} > {settings.FILE_CONTENT_TOKEN_LIMIT}). Truncating."
    )
    return truncate_to_token_limit(file_context, settings.FILE_CONTENT_TOKEN_LIMIT)


async def resolve_turn_file_ids(request: AgentRequestContext) -> list[str] | None:
    if request.file_ids:
        return request.file_ids
    if not request.session_id:
        return None
    history_service = get_chat_history_service()
    try:
        messages = await history_service.get_messages(
            session_id=request.session_id,
            limit=max(settings.CHAT_HISTORY_LIMIT, 20),
        )
    except Exception as exc:
        logger.warning(
            f"[orchestration] failed to inherit session file_ids: {exc}",
            exc_info=True,
        )
        return None
    messages.sort(key=lambda msg: str(msg.get("created_at") or ""))
    file_ids: list[str] = []
    seen: set[str] = set()
    for msg in messages:
        ids = msg.get("file_ids") or []
        if isinstance(ids, list):
            for fid in ids:
                fid_str = str(fid) if fid else ""
                if fid_str and fid_str not in seen:
                    seen.add(fid_str)
                    file_ids.append(fid_str)
    return file_ids or None


async def resolve_turn_project_id(request: AgentRequestContext) -> str | None:
    if request.project_id:
        return request.project_id
    if not request.session_id:
        return None
    history_service = get_chat_history_service()
    try:
        session = await history_service.get_session(request.session_id)
    except Exception as exc:
        logger.warning(
            f"[orchestration] failed to inherit session project_id: {exc}",
            exc_info=True,
        )
        return None
    project_id = session.get("project_id") if isinstance(session, dict) else None
    return str(project_id) if project_id else None


async def collect_file_and_project_context(
    file_ids: list[str] | None,
    user_id: str,
    query: str,
    project_id: str | None = None,
) -> tuple[str, str]:
    """Return (project-only block, combined prompt context for uploads + project).

    Workspace Milvus (``retrieve_project_related_context``) runs only when
    ``project_id`` is non-empty. Per-file Milvus uses ``file_id`` + ``user_id`` only
    (see :func:`milvus_filter_for_uploaded_file_vectors`).
    """
    project_context = ""
    pid = str(project_id).strip() if project_id is not None else ""
    if pid:
        project_context = await retrieve_project_related_context(
            query=query,
            project_id=pid,
            user_id=user_id,
        )
        if project_context:
            project_context = limit_file_context_for_llm(project_context)

    upload_context = ""
    if file_ids:
        upload_context = await collect_uploaded_file_context(
            file_ids,
            user_id,
            query,
        )
        if upload_context:
            upload_context = limit_file_context_for_llm(upload_context)

    sections = [s for s in (project_context, upload_context) if s]
    if not sections:
        return "", ""
    combined = limit_file_context_for_llm("\n\n".join(sections))
    return project_context, combined


async def retrieve_project_related_context(
    *,
    query: str,
    project_id: str,
    user_id: str,
) -> str:
    try:
        pid = str(project_id).strip()
        if not pid:
            return ""
        block = await get_retrieval_service().retrieve_project_context(
            query=query,
            project_id=pid,
            user_id=user_id,
            top_k=settings.TOP_K,
        )
    except Exception as exc:
        logger.warning(f"[orchestration] project context retrieval failed: {exc}")
        return ""
    return block or ""


def milvus_filter_for_uploaded_file_vectors(file_id: str, user_id: str) -> str:
    """Build a Milvus filter for chunks of a single uploaded file.

    **Policy:** use only ``metadata["file_id"]`` and ``metadata["user_id"]``.
    Never add ``metadata["project_id"]``, even when Mongo has a non-null
    ``project_id``: many vectors are indexed without ``project_id`` (e.g.
    message-scoped uploads), and a project clause would wrongly return no hits.
    ``file_id`` is unique per upload; that is the authoritative scope.
    """
    fid = str(file_id or "").strip()
    uid = str(user_id or "").strip()
    return f'metadata["file_id"] == "{fid}" and metadata["user_id"] == "{uid}"'


async def _retrieve_uploaded_file_context(
    *,
    file_id: str,
    user_id: str,
    query: str,
    file_name: str,
) -> str:
    """Hybrid search over Milvus chunks for one uploaded file.

    Uses :func:`milvus_filter_for_uploaded_file_vectors` only (never ``project_id``).
    """
    if not str(file_id or "").strip() or not str(user_id or "").strip():
        return ""
    if not str(query or "").strip():
        return ""

    expr = milvus_filter_for_uploaded_file_vectors(file_id, user_id)
    embedder = get_embedding_manager()
    retrieval_service = get_retrieval_service()
    try:
        embedding = await embedder.aembed_query(query)
        docs = await asyncio.to_thread(
            retrieval_service._db.search_hybrid,
            dense_vector=embedding,
            text_query=query,
            top_k=settings.FILE_SEARCH_TOP_K,
            collection_name=settings.MILVUS_PROJECT_FILES,
            expr=expr,
        )
    except Exception as exc:
        logger.warning(
            f"Milvus file context retrieval failed for file_id={file_id}, "
            f"falling back to OCR context: {exc}",
            exc_info=True,
        )
        return ""
    from app.retrieval.retrieval_service import _vector_hit_text

    chunks = []
    for index, doc in enumerate(docs or [], 1):
        text = _vector_hit_text(doc)
        if text:
            chunks.append(f"[File chunk {index}]\n{text}")
    if not chunks:
        return ""
    return (
        f"\n\n## USER FILE CONTEXT: {file_name} "
        f"(top {settings.FILE_SEARCH_TOP_K} Milvus chunks)\n" + "\n\n".join(chunks)
    )

async def collect_uploaded_file_context(
    file_ids: list[str] | None,
    user_id: str,
    query: str,
) -> str:
    history_service = get_chat_history_service()
    file_chunks: list[str] = []
    for fid in file_ids or []:
        file = await history_service.get_file_by_id(fid)
        if not file:
            continue
        metadata = file.get("file_metadata") or {}
        file_name = metadata.get("file_name", fid)
        milvus_file_index = metadata.get("milvus_file_index") or {}
        if milvus_file_index.get("enabled"):
            context = await _retrieve_uploaded_file_context(
                file_id=fid,
                user_id=user_id,
                query=query,
                file_name=file_name,
            )
            if context:
                file_chunks.append(limit_file_context_for_llm(context))
                continue
        ocr_result = file.get("ocr_result") or ""
        if ocr_result:
            file_chunks.append(
                limit_file_context_for_llm(f"{file_name}\n{ocr_result}")
            )
    if not file_chunks:
        return ""
    return limit_file_context_for_llm(
        _USER_FILE_CONTEXT_PREFIX + "\n\n" + "\n\n".join(file_chunks)
    )


async def load_turn_file_context(state: RetrievalRewriteState) -> dict[str, Any]:
    user_id = state.get("user_id", "")
    query = state["query"]
    request = AgentRequestContext(
        user_id=user_id,
        session_id=state.get("session_id", ""),
        message_id=state.get("message_id", ""),
        query=query,
        assistant="main",
        file_ids=state.get("file_ids"),
        project_id=state.get("project_id"),
    )
    try:
        # Checking for attaching previous file contexts
        resolved_file_ids = await resolve_turn_file_ids(request)
        resolved_project_id = await resolve_turn_project_id(request)

        project_context, loaded = await collect_file_and_project_context(
            resolved_file_ids,
            user_id,
            query,
            project_id=resolved_project_id,
        )

        existing = (state.get("file_context") or "").strip()
        if loaded and existing:
            combined = limit_file_context_for_llm(f"{existing}\n\n{loaded}")
        elif loaded:
            combined = loaded
        elif existing:
            combined = limit_file_context_for_llm(existing)
        else:
            return {}

        updates: dict[str, Any] = {
            "resolved_file_ids": list(resolved_file_ids or []),
            "resolved_project_id": resolved_project_id,
            "project_related_context": project_context,
            "file_context": combined,
        }
        if resolved_file_ids:
            updates["file_ids"] = resolved_file_ids
        if resolved_project_id:
            updates["project_id"] = resolved_project_id
        return updates

    except Exception as exc:
        logger.warning(
            f"[orchestration] turn file context load failed: {exc}",
            exc_info=True,
        )
        return {}


def _get_agent_registry() -> dict[str, type[Any]]:
    from app.assistants import (
        AdministrativeCourtAgent,
        CivilCourtAgent,
        ContractAnalyzerAgent,
        CourtAgent,
        CriminalCourtAgent,
        EconomicCourtAgent,
        MainAgent,
        TaxAgent,
    )

    return {
        "main": MainAgent,
        "umumiy": MainAgent,
        "tax": TaxAgent,
        "court": CourtAgent,
        "administrative_court": AdministrativeCourtAgent,
        "contract_analyzer": ContractAnalyzerAgent,
        "criminal_court": CriminalCourtAgent,
        "economic_court": EconomicCourtAgent,
        "civil_court": CivilCourtAgent,
    }


def get_chat_agent(assistant_name: str | None) -> BaseAgent:
    """Return a cached assistant agent for the canonical assistant name."""
    from app.assistants import MainAgent

    global _AGENT_REGISTRY
    canonical = AssistantConfig.validate_assistant_or_default(assistant_name)
    if canonical not in _CHAT_AGENT_CACHE:
        if _AGENT_REGISTRY is None:
            _AGENT_REGISTRY = _get_agent_registry()
        cls = _AGENT_REGISTRY.get(canonical, MainAgent)
        _CHAT_AGENT_CACHE[canonical] = cls()
    return _CHAT_AGENT_CACHE[canonical]


_AGENT_REGISTRY: dict[str, type[Any]] | None = None
_CHAT_AGENT_CACHE: dict[str, Any] = {}
