from __future__ import annotations

import asyncio
import warnings
from typing import Any

from langchain_core.messages import (
    AIMessage,
    BaseMessage,
    ToolMessage,
)

from app.core.assistants import AssistantConfig
from app.core.config import settings
from app.core.dependencies import (
    get_chat_history_service,
    get_prompt_registry,
)
from app.core.logger import logger
from app.llms.lanchain import LangChain

_agent_checkpointer: Any = None  # AsyncRedisSaver
_checkpointer_needs_async_close = False


class LangGraphRedisCheckpointerError(RuntimeError):
    """Raised when LangGraph AsyncRedisSaver is required but unavailable."""


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
    """LangGraph Redis saver expects ``default_ttl`` in minutes."""
    secs = max(int(settings.LANGGRAPH_CHECKPOINT_TTL_SECONDS), 60)
    return float(secs) / 60.0


def agent_session_thread_id(user_id: str, session_id: str) -> str:
    """Stable LangGraph thread id for one chat session."""
    return f"{user_id}:{session_id}"


def agent_turn_thread_id(
    user_id: str, session_id: str, assistant_name: str, message_id: str
) -> str:
    """
    Legacy per-message thread id.

    Chat turns now use ``agent_session_thread_id`` so LangGraph checkpoints carry
    conversational memory across follow-up questions. Keep this helper for any
    debug tooling or external callers that still reference old checkpoint keys.
    """
    safe_asst = AssistantConfig.validate_assistant_or_default(assistant_name)
    return f"{user_id}:{session_id}:{safe_asst}:{message_id}"


async def delete_agent_thread(*, user_id: str, session_id: str) -> None:
    """
    Remove LangGraph checkpoint state for this chat session in Redis.

    Best-effort: never raises; logs a warning on failure. Call after Mongo session
    delete if you need immediate cleanup; otherwise Redis TTL still expires keys.
    """
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
    """Initialize the required LangGraph AsyncRedisSaver checkpointer."""
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
    """Call from FastAPI lifespan shutdown."""
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


def _format_main_system_instructions() -> str:
    tmpl = get_prompt_registry().get_assistant_prompt("main")
    return tmpl.format(
        context="",
        chat_history="Earlier turns are kept in conversation messages for this thread.",
    )


def _compile_lc_agent(*, system_prompt: str) -> Any:
    """Build a fresh compiled graph for checkpoint reads and final turns."""
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
    """JSON-friendly view of one checkpoint message (for logs / debug)."""
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


async def aget_general_agent_state_snapshot(
    thread_id: str,
    *,
    max_content_len: int = 4000,
) -> dict[str, Any]:
    """
    Read the latest LangGraph checkpoint for ``thread_id`` from AsyncRedisSaver.

    Empty ``messages`` if that thread has never run in this process.

    Uses a nominal ``main`` agent shape; checkpoints created with other agents
    may not load if graph identity does not match the saver backend.
    """
    agent = _compile_lc_agent(system_prompt=_format_main_system_instructions())
    config = {"configurable": {"thread_id": thread_id}}
    snap = await agent.aget_state(config)
    values: dict[str, Any] = snap.values if isinstance(snap.values, dict) else {}
    messages_raw = values.get("messages") or []
    messages_out = [
        serialize_graph_message(m, max_content_len=max_content_len)
        for m in messages_raw
        if isinstance(m, BaseMessage)
    ]
    meta = snap.metadata
    meta_out: dict[str, Any] | None = None
    if meta is not None:
        if isinstance(meta, dict):
            meta_out = dict(meta)
        else:
            meta_out = {"repr": repr(meta)}

    return {
        "thread_id": thread_id,
        "next": list(snap.next),
        "created_at": snap.created_at,
        "metadata": meta_out,
        "state_keys": list(values.keys()),
        "remaining_steps": values.get("remaining_steps"),
        "message_count": len(messages_out),
        "messages": messages_out,
    }


def _format_checkpoint_rows_for_chat_history(
    rows: list[dict[str, Any]], thread_id: str
) -> str:
    """Turn serialized checkpoint messages into plain text for ``{chat_history}`` prompts."""
    parts: list[str] = [
        f"Conversation from LangGraph checkpoint (thread_id={thread_id}):"
    ]
    for row in rows:
        role = str(row.get("type") or "message")
        body = row.get("content")
        if body is None:
            body = row.get("content_preview")
        if not isinstance(body, str):
            body = str(body) if body is not None else ""
        body = body.strip()
        if not body:
            continue
        parts.append(f"[{role}]\n{body}")
    if len(parts) <= 1:
        return ""
    return "\n\n".join(parts).strip()


async def _mongo_recent_turns_as_chat_history(session_id: str) -> str:
    """Same shape as ``BaseAgent._get_session_history_text`` (Mongo recent messages)."""
    if not session_id:
        return ""
    svc = get_chat_history_service()
    messages = await svc.get_recent_messages(
        session_id=session_id,
        limit=int(settings.CHAT_HISTORY_LIMIT or 5),
    )
    entries: list[tuple[str, str]] = []
    for entry in messages or []:
        content = entry.get("content") or {}
        question = content.get("query")
        answer = content.get("response")
        if question and answer:
            entries.append((str(question), str(answer)))
    if not entries:
        return ""
    lines = ["Previous Conversation History:"]
    for i, (question, answer) in enumerate(entries, 1):
        lines.append(f"Turn {i}:")
        lines.append(f"  User: {question}")
        lines.append(f"  Assistant: {answer}")
    lines.append(
        "Note: numbered items inside an Assistant message (for example follow-up "
        "questions 1. ... 2. ...) are not turn numbers."
    )
    lines.append("Use the above conversation to maintain context and consistency.")
    return "\n".join(lines)


async def build_retrieval_thread_chat_history(user_id: str, session_id: str) -> str:
    """
    Read-only session history for retrieval query rewrite.

    Prefers Mongo recent turns (no Redis checkpoint compile) and falls back to
    checkpoint snapshots when Mongo has no turns yet.
    """
    if not session_id:
        return ""
    hist = await _mongo_recent_turns_as_chat_history(session_id)
    if hist.strip():
        return hist
    thread_id = agent_session_thread_id(user_id, session_id)
    try:
        snap = await aget_general_agent_state_snapshot(thread_id, max_content_len=6000)
        rows = snap.get("messages") or []
        return _format_checkpoint_rows_for_chat_history(rows, thread_id)
    except Exception as exc:
        logger.debug(
            "[pipeline] LangGraph retrieval history read skipped (%s): %s",
            thread_id,
            exc,
        )
        return ""


async def build_pipeline_thread_chat_history(user_id: str, session_id: str) -> str:
    """
    Legacy text formatter for checkpoint or Mongo history (debug / fallback).

    Final LLM turns keep history on the LangGraph ``thread_id``; retrieval context
    and file context are injected into the system prompt instead.
    """
    if not session_id:
        return ""
    thread_id = agent_session_thread_id(user_id, session_id)
    try:
        snap = await aget_general_agent_state_snapshot(thread_id, max_content_len=6000)
        rows = snap.get("messages") or []
        hist = _format_checkpoint_rows_for_chat_history(rows, thread_id)
        if hist:
            return hist
    except Exception as exc:
        logger.debug(
            "[pipeline] LangGraph chat history read skipped (%s): %s",
            thread_id,
            exc,
        )
    return await _mongo_recent_turns_as_chat_history(session_id)
