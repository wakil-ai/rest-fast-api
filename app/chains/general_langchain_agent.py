"""
LangGraph + LangChain pipeline for the general (main / umumiy) assistant only.

LLM (Gemini), tool calling (Lexuz retrieval + optional Tavily), and session memory
(thread-scoped checkpoints) are wired through LangGraph's prebuilt react agent.

**Graph state (``create_react_agent`` default)** — LangGraph’s ``AgentState``-style dict:

- ``messages``: list of ``HumanMessage`` / ``AIMessage`` / ``ToolMessage``, merged with
  ``add_messages`` so each turn appends to the transcript for that ``thread_id``.
- ``remaining_steps``: optional cap on agent steps (prebuilt react agent).

```mermaid
flowchart LR
  START([START]) --> preprompt[Prompt + system]
  preprompt --> model[ChatGemini]
  model --> tools{tool calls?}
  tools -->|yes| toolnode[ToolNode]
  toolnode --> model
  tools -->|no| END([END])
```

Checkpoint storage: **Redis** (``AsyncRedisSaver``) when
``LANGGRAPH_CHECKPOINT_USE_REDIS`` is true and setup succeeds; otherwise
``MemorySaver`` (in-process). Redis checkpoints use ``LANGGRAPH_CHECKPOINT_TTL_SECONDS`` (default 3 days) so keys auto-expire; session delete also removes the thread immediately.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncGenerator
from typing import Any

from langchain_core.messages import (
    AIMessage,
    AIMessageChunk,
    BaseMessage,
    HumanMessage,
    ToolMessage,
)
from langchain_core.prompts import ChatPromptTemplate, MessagesPlaceholder
from langchain_core.tools import tool
from langchain_google_genai import ChatGoogleGenerativeAI
from langgraph.checkpoint.memory import MemorySaver
from langgraph.prebuilt import create_react_agent

from app.assistants.main import MainAssistant
from app.core.config import settings
from app.core.dependencies import get_prompt_registry
from app.core.logger import logger

try:
    from langchain_community.tools.tavily_search import TavilySearchResults
except ImportError:
    TavilySearchResults = None  # type: ignore[misc, assignment]

_general_checkpointer: Any = None  # MemorySaver | AsyncRedisSaver
_compiled_general_agent = None
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


def langgraph_thread_id(user_id: str, session_id: str) -> str:
    """Must match ``thread_id`` passed to chat invoke (``user_id:resolved_session_id``)."""
    return f"{user_id}:{session_id}"


async def delete_general_agent_thread(*, user_id: str, session_id: str) -> None:
    """
    Remove LangGraph checkpoint state for this chat session (Redis or MemorySaver).

    Best-effort: never raises; logs a warning on failure. Call after Mongo session
    delete if you need immediate cleanup; otherwise Redis TTL still expires keys.
    """
    thread_id = langgraph_thread_id(user_id, session_id)
    try:
        cp = _get_general_checkpointer()
        adelete = getattr(cp, "adelete_thread", None)
        if adelete is not None:
            await adelete(thread_id)
            logger.info("[general_lc] Deleted LangGraph thread %s", thread_id)
            return
        sync_del = getattr(cp, "delete_thread", None)
        if sync_del is not None:
            await asyncio.to_thread(sync_del, thread_id)
            logger.info("[general_lc] Deleted LangGraph thread %s (sync)", thread_id)
    except Exception:
        logger.warning(
            "[general_lc] Failed to delete LangGraph thread %s",
            thread_id,
            exc_info=True,
        )


async def init_langgraph_checkpointer() -> None:
    """Call from FastAPI lifespan startup. Reconfigures checkpointer and clears compiled graph."""
    global _general_checkpointer, _compiled_general_agent, _checkpointer_needs_async_close

    _compiled_general_agent = None
    _checkpointer_needs_async_close = False

    if not getattr(settings, "LANGGRAPH_CHECKPOINT_USE_REDIS", False):
        _general_checkpointer = MemorySaver()
        logger.info("[general_lc] LangGraph checkpointer: MemorySaver (LANGGRAPH_CHECKPOINT_USE_REDIS=false)")
        return

    try:
        from langgraph.checkpoint.redis.aio import AsyncRedisSaver
    except ImportError:
        logger.warning(
            "[general_lc] langgraph-checkpoint-redis not installed; using MemorySaver"
        )
        _general_checkpointer = MemorySaver()
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
        _general_checkpointer = saver
        _checkpointer_needs_async_close = True
        log_url = url.split("@")[-1] if "@" in url else url
        logger.info(
            "[general_lc] LangGraph checkpointer: AsyncRedisSaver (%s, ttl≈%.0f min, %ds)",
            log_url,
            ttl_minutes,
            int(getattr(settings, "LANGGRAPH_CHECKPOINT_TTL_SECONDS", 86400 * 3)),
        )
    except Exception:
        logger.exception(
            "[general_lc] Redis checkpointer setup failed; falling back to MemorySaver "
            "(ensure Redis supports RedisJSON + search modules as required by langgraph-checkpoint-redis)"
        )
        _general_checkpointer = MemorySaver()


async def shutdown_langgraph_checkpointer() -> None:
    """Call from FastAPI lifespan shutdown."""
    global _general_checkpointer, _compiled_general_agent, _checkpointer_needs_async_close

    _compiled_general_agent = None
    cp = _general_checkpointer
    should_close = _checkpointer_needs_async_close
    _general_checkpointer = None
    _checkpointer_needs_async_close = False

    if cp is not None and should_close:
        await cp.__aexit__(None, None, None)


def _get_general_checkpointer() -> Any:
    global _general_checkpointer
    if _general_checkpointer is None:
        _general_checkpointer = MemorySaver()
        logger.warning(
            "[general_lc] Checkpointer was unset; created MemorySaver (lifespan init missing?)"
        )
    return _general_checkpointer


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


async def _search_lexuz_impl(search_query: str) -> str:
    logger.info(f"[general_lc] Lexuz search: {search_query}")
    assistant = MainAssistant()
    try:
        result = await assistant.retrieve(
            query=search_query,
            file_context="",
            chat_history="",
        )
        text = result.context.strip() if result.context else ""
        return text if text else "No relevant corpus passages were returned."
    except Exception as e:
        logger.warning(f"[general_lc] Lexuz retrieval failed: {e}", exc_info=True)
        return "Lexuz search failed temporarily; retry or answer from general reasoning where appropriate."


@tool
async def search_lexuz_legal_documents(search_query: str) -> str:
    """Hybrid search Uzbekistan legal corpus (Lexuz-style KB). Prefer focused legal keywords."""
    return await _search_lexuz_impl(search_query)


def _build_tools() -> list[Any]:
    tools: list[Any] = [search_lexuz_legal_documents]
    if settings.TAVILY_API_KEY and TavilySearchResults is not None:
        tools.append(
            TavilySearchResults(
                api_key=settings.TAVILY_API_KEY,
                max_results=5,
            )
        )
    return tools


def _build_lc_llm() -> ChatGoogleGenerativeAI:
    if not getattr(settings, "GEMINI_API_KEY", None):
        raise RuntimeError(
            "GEMINI_API_KEY is required for the LangChain / LangGraph general assistant route."
        )
    return ChatGoogleGenerativeAI(
        model=_gemini_lc_model_name(),
        google_api_key=settings.GEMINI_API_KEY,
        temperature=settings.TEMPERATURE,
        max_output_tokens=settings.OUTPUT_MAX_TOKENS,
        thinking_level="high",
    )


def _get_compiled_general_agent():  # type: ignore[no-untyped-def]
    global _compiled_general_agent
    if _compiled_general_agent is None:
        llm = _build_lc_llm()
        system = _format_main_system_instructions()
        prompt = ChatPromptTemplate.from_messages(
            [
                ("system", system),
                MessagesPlaceholder("messages"),
            ]
        )
        _compiled_general_agent = create_react_agent(
            llm,
            _build_tools(),
            prompt=prompt,
            checkpointer=_get_general_checkpointer(),
        )
        logger.info(
            "[general_lc] Compiled LangGraph react agent (Gemini=%s)",
            _gemini_lc_model_name(),
        )
    return _compiled_general_agent


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
    """
    agent = _get_compiled_general_agent()
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


def _last_ai_text(messages: list[BaseMessage]) -> str:
    for msg in reversed(messages):
        if isinstance(msg, AIMessage):
            text = _message_text(msg)
            if text.strip():
                return text
    return ""


async def invoke_general_lc_agent(
    *,
    thread_id: str,
    query: str,
    file_context: str = "",
    user_id_for_logs: str = "",
) -> tuple[str, dict[str, Any]]:
    agent = _get_compiled_general_agent()
    payload = query.strip()
    if file_context.strip():
        payload = (
            f"{file_context.strip()}\n\n---\nUser question:\n{query.strip()}"
        )

    config = {"configurable": {"thread_id": thread_id}}
    outcome = await agent.ainvoke(
        {"messages": [HumanMessage(content=payload)]},
        config,
    )
    msgs: list[BaseMessage] = list(outcome.get("messages") or [])
    answer = _last_ai_text(msgs)

    meta: dict[str, Any] = {}
    usage = getattr(msgs[-1], "usage_metadata", None) if msgs else None
    if isinstance(usage, dict):
        meta["token_usage"] = usage

    meta["model"] = _gemini_lc_model_name()
    meta["workflow"] = "langgraph_react_gemini_general"
    meta["attachments"] = None
    if user_id_for_logs:
        meta["lc_user_ref"] = user_id_for_logs
    return answer or "(empty model response)", meta


async def astream_general_lc_agent(
    *,
    thread_id: str,
    query: str,
    file_context: str = "",
) -> AsyncGenerator[str | dict[str, Any], None]:
    agent = _get_compiled_general_agent()
    payload = query.strip()
    if file_context.strip():
        payload = (
            f"{file_context.strip()}\n\n---\nUser question:\n{query.strip()}"
        )

    config = {"configurable": {"thread_id": thread_id}}

    collected_meta: dict[str, Any] = {
        "model": _gemini_lc_model_name(),
        "workflow": "langgraph_react_gemini_general_stream",
        "attachments": None,
    }

    input_state = {"messages": [HumanMessage(content=payload)]}

    async for event in agent.astream(
        input_state,
        config,
        stream_mode="messages",
    ):
        chunk: AIMessageChunk | BaseMessage | Any = event
        if isinstance(event, tuple) and event:
            chunk = event[0]

        if isinstance(chunk, AIMessageChunk):
            piece = _message_text(chunk)
            if piece:
                yield piece
            usage = getattr(chunk, "usage_metadata", None)
            if isinstance(usage, dict):
                collected_meta["token_usage"] = usage

        if isinstance(chunk, AIMessage):
            usage = getattr(chunk, "usage_metadata", None)
            if isinstance(usage, dict):
                collected_meta["token_usage"] = usage

    yield {"type": "_generation_meta", "meta": collected_meta}