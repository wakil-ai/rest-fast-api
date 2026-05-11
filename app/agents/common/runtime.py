from __future__ import annotations

import asyncio
from collections.abc import AsyncGenerator, Iterator
from typing import Any

from langchain_core.messages import (
    AIMessage,
    AIMessageChunk,
    BaseMessage,
    HumanMessage,
    ToolMessage,
)
from langchain.agents import create_agent
from langchain_core.tools import tool
from langchain_google_genai import ChatGoogleGenerativeAI
from langgraph.checkpoint.memory import MemorySaver

from app.core.assistants import AssistantConfig
from app.core.config import settings
from app.core.dependencies import get_chat_orchestrator, get_prompt_registry
from app.core.logger import logger

try:
    from langchain_community.tools.tavily_search import TavilySearchResults
except ImportError:
    TavilySearchResults = None  # type: ignore[misc, assignment]

_agent_checkpointer: Any = None  # MemorySaver | AsyncRedisSaver
_checkpointer_needs_async_close = False


def _build_langgraph_redis_url() -> str:
    uri = getattr(settings, "REDIS_URI", None)
    if isinstance(uri, str) and uri.strip():
        return uri.strip()
    password = getattr(settings, "REDIS_PASSWORD", None)
    if isinstance(password, str) and password:
        return (
            f"redis://:{password}@{settings.REDIS_HOST}:{settings.REDIS_PORT}/0"
        )
    return f"redis://{settings.REDIS_HOST}:{settings.REDIS_PORT}/0"


def _default_ttl_minutes_for_checkpoints() -> float:
    """LangGraph Redis saver expects ``default_ttl`` in minutes."""
    secs = max(int(getattr(settings, "LANGGRAPH_CHECKPOINT_TTL_SECONDS", 86400 * 3)), 60)
    return float(secs) / 60.0


def agent_session_thread_id(user_id: str, session_id: str) -> str:
    """Must match ``thread_id`` passed to chat invoke (``user_id:resolved_session_id``)."""
    return f"{user_id}:{session_id}"


def agent_turn_thread_id(
    user_id: str, session_id: str, assistant_name: str, message_id: str
) -> str:
    """
    One LangGraph thread per user message.

    ``GenerationContext`` already embeds Mongo session history in ``system_prompt``,
    so checkpoint state must not span multiple HTTP turns (which would duplicate
    history). Ephemeral keys expire via Redis TTL; session delete removes only
    ``agent_session_thread_id`` (legacy).
    """
    safe_asst = AssistantConfig.validate_assistant_or_default(assistant_name)
    return f"{user_id}:{session_id}:{safe_asst}:{message_id}"


async def delete_agent_thread(*, user_id: str, session_id: str) -> None:
    """
    Remove LangGraph checkpoint state for this chat session (Redis or MemorySaver).

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
    """Call from FastAPI lifespan startup. Reconfigures checkpointer."""
    global _agent_checkpointer, _checkpointer_needs_async_close

    _checkpointer_needs_async_close = False

    if not getattr(settings, "LANGGRAPH_CHECKPOINT_USE_REDIS", False):
        _agent_checkpointer = MemorySaver()
        logger.info("[chat_agent] LangGraph checkpointer: MemorySaver (LANGGRAPH_CHECKPOINT_USE_REDIS=false)")
        return

    try:
        from langgraph.checkpoint.redis.aio import AsyncRedisSaver
    except ImportError:
        logger.warning(
            "[chat_agent] langgraph-checkpoint-redis not installed; using MemorySaver"
        )
        _agent_checkpointer = MemorySaver()
        return

    url = _build_langgraph_redis_url()
    ttl_minutes = _default_ttl_minutes_for_checkpoints()
    try:
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
        ttl_seconds = int(getattr(settings, "LANGGRAPH_CHECKPOINT_TTL_SECONDS", 86400 * 3))
        logger.info(
            f"[chat_agent] LangGraph checkpointer: AsyncRedisSaver ({log_url}, "
            f"ttl≈{ttl_minutes:.0f} min, {ttl_seconds}s)"
        )
    except Exception:
        logger.exception(
            "[chat_agent] Redis checkpointer setup failed; falling back to MemorySaver "
            "(ensure Redis supports RedisJSON + search modules as required by langgraph-checkpoint-redis)"
        )
        _agent_checkpointer = MemorySaver()


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
        _agent_checkpointer = MemorySaver()
        logger.warning(
            "[chat_agent] Checkpointer was unset; created MemorySaver (lifespan init missing?)"
        )
    return _agent_checkpointer


def _gemini_lc_model_name() -> str:
    raw = settings.GEMINI_LANGCHAIN_CHAT_MODEL
    if isinstance(raw, str) and raw.strip():
        return raw.strip()
    m = getattr(settings, "DEFAULT_CHAT_MODEL", "") or ""
    if isinstance(m, str) and m.strip().startswith("gemini"):
        return m.strip()
    return "gemini-2.5-flash"


def _format_main_system_instructions() -> str:
    tmpl = get_prompt_registry().get_assistant_prompt("main")
    return tmpl.format(
        context=(
            "Substantive answers must rely on Uzbek legal corpus text retrieved with the "
            "`search_lexuz_legal_documents` tool when the question calls for statutes or "
            "codified norms. If web search is enabled, use it for current events or facts "
            "not in the corpus."
        ),
        chat_history="Earlier turns are kept in conversation messages for this thread.",
    )


async def _search_lexuz_via_assistant_registry(
    search_query: str,
    *,
    assistant_name: str,
    court_route_tag: str | None = None,
) -> str:
    """Delegates to ``ChatOrchestrator._get_agent`` (same as ``chat_orchestrator.py`` retrieval)."""
    orchestrator = get_chat_orchestrator()
    inst = orchestrator._get_agent(assistant_name)
    logger.info(f"[chat_agent] Lexuz tool search ({assistant_name}): {search_query}")
    try:
        kwargs: dict[str, Any] = {}
        if court_route_tag:
            kwargs["court_route_tag"] = court_route_tag
        result = await inst.retrieve(
            query=search_query,
            file_context="",
            chat_history="",
            **kwargs,
        )
        text = result.context.strip() if result.context else ""
        return text if text else "No relevant corpus passages were returned."
    except Exception as e:
        logger.warning(f"[chat_agent] Lexuz retrieval failed: {e}", exc_info=True)
        return (
            "Lexuz search failed temporarily; retry or answer from general reasoning "
            "where appropriate."
        )


def build_chat_agent_tools(
    assistant_name: str,
    *,
    court_route_tag: str | None = None,
) -> list[Any]:
    canonical = AssistantConfig.validate_assistant_or_default(assistant_name)

    @tool
    async def search_lexuz_legal_documents(search_query: str) -> str:
        """Hybrid search Uzbekistan legal corpus (Lexuz-style KB). Prefer focused legal keywords."""
        return await _search_lexuz_via_assistant_registry(
            search_query,
            assistant_name=canonical,
            court_route_tag=court_route_tag,
        )

    tools: list[Any] = [search_lexuz_legal_documents]
    if settings.TAVILY_API_KEY and TavilySearchResults is not None:
        tools.append(
            TavilySearchResults(
                api_key=settings.TAVILY_API_KEY,
                max_results=5,
            )
        )
    return tools


def _compile_lc_agent(
    *,
    system_prompt: str,
    assistant_name: str,
    court_route_tag: str | None = None,
) -> Any:
    """Build a fresh compiled graph (system prompt and tools vary by assistant / turn)."""
    llm = _build_lc_llm()
    tools = build_chat_agent_tools(
        assistant_name, court_route_tag=court_route_tag
    )
    return create_agent(
        llm,
        tools,
        system_prompt=system_prompt,
        checkpointer=_get_agent_checkpointer(),
    )


def _build_lc_llm() -> ChatGoogleGenerativeAI:
    if not getattr(settings, "GEMINI_API_KEY", None):
        raise RuntimeError(
            "GEMINI_API_KEY is required for the LangChain / LangGraph chat agent route."
        )
    level = getattr(settings, "GEMINI_LANGCHAIN_THINKING_LEVEL", None) or "low"
    level = str(level).strip().lower()
    if level in ("off", "false", "0", "none"):
        level = "minimal"
    allowed = frozenset({"minimal", "low", "medium", "high"})
    if level not in allowed:
        level = "low"
    return ChatGoogleGenerativeAI(
        model=_gemini_lc_model_name(),
        google_api_key=settings.GEMINI_API_KEY,
        temperature=settings.TEMPERATURE,
        max_output_tokens=settings.OUTPUT_MAX_TOKENS,
        thinking_level=level,
        # Force streaming API on ainvoke so token callbacks fire for LangGraph stream_mode="messages".
        streaming=True,
    )


def _truncate(s: str, max_len: int) -> str:
    if len(s) <= max_len:
        return s
    return s[: max_len - 3] + "..."


def serialize_graph_message(msg: BaseMessage, *, max_content_len: int = 4000) -> dict[str, Any]:
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
    Read the latest LangGraph checkpoint for ``thread_id`` from ``MemorySaver``.

    Empty ``messages`` if that thread has never run in this process.

    Uses a nominal ``main`` agent shape; checkpoints created with other agents
    may not load if graph identity does not match the saver backend.
    """
    agent = _compile_lc_agent(
        system_prompt=_format_main_system_instructions(),
        assistant_name="main",
        court_route_tag=None,
    )
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


def _message_text(msg: AIMessage | AIMessageChunk) -> str:
    content = getattr(msg, "content", None)
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: list[str] = []
        for block in content:
            if isinstance(block, str):
                parts.append(block)
            elif isinstance(block, dict) and "text" in block:
                parts.append(str(block["text"]))
        return "".join(parts)
    return ""


def _stream_messages_event_to_message(event: Any) -> BaseMessage | None:
    """
    Normalize LangGraph ``stream_mode=\"messages\"`` payloads (v1 tuples vs v2 dicts).
    """
    if isinstance(event, dict) and event.get("type") == "messages":
        data = event.get("data")
        if isinstance(data, tuple) and data and isinstance(data[0], BaseMessage):
            return data[0]
        if isinstance(data, list) and data and isinstance(data[0], BaseMessage):
            return data[0]
        return None
    if isinstance(event, tuple) and event:
        if isinstance(event[0], BaseMessage):
            return event[0]
        if (
            len(event) == 3
            and event[1] == "messages"
            and isinstance(event[2], tuple)
            and event[2]
            and isinstance(event[2][0], BaseMessage)
        ):
            return event[2][0]
        if (
            len(event) == 2
            and isinstance(event[1], tuple)
            and event[1]
            and isinstance(event[1][0], BaseMessage)
        ):
            return event[1][0]
    if isinstance(event, BaseMessage):
        return event
    return None


def _sse_text_slice_limit() -> int:
    try:
        n = int(getattr(settings, "STREAM_SSE_MAX_RESPONSE_CHARS", 200))
    except (TypeError, ValueError):
        n = 200
    return max(48, min(n, 4096))


def _iter_text_slices_for_sse(text: str, *, max_chars: int) -> Iterator[str]:
    """Split a large model delta into multiple strings for separate SSE chunk events."""
    if not text:
        return
    if len(text) <= max_chars:
        yield text
        return
    i = 0
    n = len(text)
    min_break = max(16, max_chars // 3)
    while i < n:
        end = min(i + max_chars, n)
        if end < n:
            window = text[i:end]
            br = window.rfind("\n")
            if br >= min_break:
                end = i + br + 1
            else:
                sp = window.rfind(" ")
                if sp >= min_break:
                    end = i + sp + 1
        yield text[i:end]
        i = end


def _visible_text_piece(msg: AIMessage | AIMessageChunk) -> str:
    """Prefer LangChain normalized ``.text`` (text blocks only), then legacy extraction."""
    t = getattr(msg, "text", None)
    if t is not None:
        s = str(t)
        if s:
            return s
    return _message_text(msg)


def _delta_stream_text(*, previous: str, piece: str) -> tuple[str, str]:
    """
    Return (delta_to_emit, new_cumulative) for LLM chunks that may be token-deltas
    or growing cumulative strings.
    """
    if not piece:
        return "", previous
    if previous and piece.startswith(previous):
        return piece[len(previous) :], piece
    return piece, previous + piece


def _last_ai_text(messages: list[BaseMessage]) -> str:
    for msg in reversed(messages):
        if isinstance(msg, AIMessage):
            text = _message_text(msg)
            if text.strip():
                return text
    return ""


async def invoke_chat_agent(
    *,
    thread_id: str,
    query: str,
    system_prompt: str,
    assistant_name: str,
    court_route_tag: str | None = None,
    user_id_for_logs: str = "",
) -> tuple[str, dict[str, Any]]:
    canonical = AssistantConfig.validate_assistant_or_default(assistant_name)
    agent = _compile_lc_agent(
        system_prompt=system_prompt,
        assistant_name=canonical,
        court_route_tag=court_route_tag,
    )

    config = {"configurable": {"thread_id": thread_id}}
    outcome = await agent.ainvoke(
        {"messages": [HumanMessage(content=query.strip())]},
        config,
    )
    msgs: list[BaseMessage] = list(outcome.get("messages") or [])
    answer = _last_ai_text(msgs)

    meta: dict[str, Any] = {}
    usage = getattr(msgs[-1], "usage_metadata", None) if msgs else None
    if isinstance(usage, dict):
        meta["token_usage"] = usage

    meta["model"] = _gemini_lc_model_name()
    meta["workflow"] = f"langgraph_react_gemini_{canonical}"
    meta["langgraph_assistant"] = canonical
    meta["attachments"] = None
    if user_id_for_logs:
        meta["lc_user_ref"] = user_id_for_logs
    return answer or "(empty model response)", meta


async def astream_chat_agent(
    *,
    thread_id: str,
    query: str,
    system_prompt: str,
    assistant_name: str,
    court_route_tag: str | None = None,
) -> AsyncGenerator[str | dict[str, Any], None]:
    canonical = AssistantConfig.validate_assistant_or_default(assistant_name)
    agent = _compile_lc_agent(
        system_prompt=system_prompt,
        assistant_name=canonical,
        court_route_tag=court_route_tag,
    )

    config = {"configurable": {"thread_id": thread_id}}

    collected_meta: dict[str, Any] = {
        "model": _gemini_lc_model_name(),
        "workflow": f"langgraph_react_gemini_{canonical}_stream",
        "langgraph_assistant": canonical,
        "attachments": None,
    }

    input_state = {"messages": [HumanMessage(content=query.strip())]}
    emitted_text = False
    final_ai_text = ""
    streamed_answer_prefix = ""
    sse_slice = _sse_text_slice_limit()

    # LangGraph: subgraphs=True so StreamMessagesHandler registers model runs (see
    # langgraph/pregel/_messages.py). version="v2": unified StreamPart dicts as in
    # https://docs.langchain.com/oss/python/langchain/streaming
    async for event in agent.astream(
        input_state,
        config,
        stream_mode="messages",
        subgraphs=True,
        version="v2",
    ):
        chunk: BaseMessage | None = None
        if isinstance(event, dict) and event.get("type") == "messages":
            data = event.get("data")
            if isinstance(data, (tuple, list)) and data and isinstance(
                data[0], BaseMessage
            ):
                chunk = data[0]
        if chunk is None:
            chunk = _stream_messages_event_to_message(event)
        if chunk is None:
            chunk = event if isinstance(event, BaseMessage) else None
        if chunk is None or not isinstance(chunk, BaseMessage):
            continue

        if isinstance(chunk, AIMessageChunk):
            piece = _visible_text_piece(chunk)
            if piece:
                delta, streamed_answer_prefix = _delta_stream_text(
                    previous=streamed_answer_prefix,
                    piece=piece,
                )
                if delta:
                    emitted_text = True
                    for part in _iter_text_slices_for_sse(delta, max_chars=sse_slice):
                        yield part
            usage = getattr(chunk, "usage_metadata", None)
            if isinstance(usage, dict):
                collected_meta["token_usage"] = usage

        elif isinstance(chunk, AIMessage):
            text = _visible_text_piece(chunk)
            if text.strip():
                final_ai_text = text
            usage = getattr(chunk, "usage_metadata", None)
            if isinstance(usage, dict):
                collected_meta["token_usage"] = usage

    if not emitted_text and final_ai_text:
        for part in _iter_text_slices_for_sse(final_ai_text, max_chars=sse_slice):
            yield part

    yield {"type": "_generation_meta", "meta": collected_meta}
