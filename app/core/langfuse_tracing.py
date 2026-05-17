"""Langfuse observability for LangChain / LangGraph LLM calls."""

from __future__ import annotations

import os
from contextlib import asynccontextmanager, contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from typing import Any

from app.core.config import settings
from app.core.logger import logger

_langfuse_env_configured = False
_llm_trace_ctx: ContextVar[LlmTraceContext | None] = ContextVar(
    "llm_trace_ctx", default=None
)


@dataclass(frozen=True, slots=True)
class LlmTraceContext:
    user_id: str = ""
    session_id: str = ""
    tags: tuple[str, ...] = ()


class LlmRunName:
    """Human-readable Langfuse trace / run names."""

    ORCHESTRATION = "orchestration"
    INTENT_RECOGNITION = "intent recognition"
    COURT_ROUTING = "court routing"
    CRIMINAL_MODE_ROUTING = "criminal mode routing"
    QUERY_REWRITE = "query rewrite"
    MILVUS_FILTER = "milvus filter"
    CYPHER_FILTER_PLANNING = "cypher filter planning"
    CRIMINAL_CASE_RETRIEVAL = "criminal case retrieval"
    CONTEXT_EVALUATION = "context evaluation"
    FINAL_ANSWER = "final answer"
    CRIMINAL_COURT_ANSWER = "criminal court answer"
    ASSISTANT_GENERATION = "assistant generation"


def langfuse_base_url() -> str | None:
    explicit = (settings.LANGFUSE_BASE_URL or "").strip()
    if explicit:
        return explicit.rstrip("/")
    host = (settings.LANGFUSE_HOST or "").strip()
    if host:
        return host.rstrip("/")
    return None


def is_langfuse_enabled() -> bool:
    if not settings.LANGFUSE_TRACING_ENABLED:
        return False
    return bool(
        (settings.LANGFUSE_PUBLIC_KEY or "").strip()
        and (settings.LANGFUSE_SECRET_KEY or "").strip()
        and langfuse_base_url()
    )


def configure_langfuse_env() -> None:
    """Map app settings to Langfuse SDK environment variables (idempotent)."""
    global _langfuse_env_configured
    if _langfuse_env_configured:
        return
    _langfuse_env_configured = True
    if not is_langfuse_enabled():
        logger.info("Langfuse tracing disabled (missing keys, host, or LANGFUSE_TRACING_ENABLED=false)")
        return

    os.environ.setdefault("LANGFUSE_PUBLIC_KEY", settings.LANGFUSE_PUBLIC_KEY.strip())
    os.environ.setdefault("LANGFUSE_SECRET_KEY", settings.LANGFUSE_SECRET_KEY.strip())
    base = langfuse_base_url()
    if base:
        os.environ.setdefault("LANGFUSE_BASE_URL", base)
    logger.info("Langfuse tracing enabled (host={})", base)


def flush_langfuse() -> None:
    if not is_langfuse_enabled():
        return
    configure_langfuse_env()
    try:
        from langfuse import get_client

        get_client().flush()
    except Exception as exc:
        logger.warning("Langfuse flush failed: {}", exc)


def flush_langfuse_if_enabled() -> None:
    """Push batched observations to Langfuse (call after a chat turn)."""
    flush_langfuse()


@contextmanager
def llm_trace_context(
    *,
    user_id: str = "",
    session_id: str = "",
    tags: tuple[str, ...] | list[str] = (),
):
    tag_tuple = tuple(tags) if tags else ()
    token = _llm_trace_ctx.set(
        LlmTraceContext(
            user_id=(user_id or "").strip(),
            session_id=(session_id or "").strip(),
            tags=tag_tuple,
        )
    )
    try:
        yield
    finally:
        _llm_trace_ctx.reset(token)


@asynccontextmanager
async def allm_trace_context(
    *,
    user_id: str = "",
    session_id: str = "",
    tags: tuple[str, ...] | list[str] = (),
):
    with llm_trace_context(user_id=user_id, session_id=session_id, tags=tags):
        yield


def _current_trace() -> LlmTraceContext:
    return _llm_trace_ctx.get() or LlmTraceContext()


def _callback_handler():
    configure_langfuse_env()
    from langfuse.langchain import CallbackHandler

    return CallbackHandler()


def langchain_invoke_config(
    run_name: str,
    *,
    user_id: str | None = None,
    session_id: str | None = None,
    tags: list[str] | None = None,
    metadata: dict[str, Any] | None = None,
    base_config: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """LangChain RunnableConfig with Langfuse callback, run name, and session metadata."""
    if not is_langfuse_enabled():
        return dict(base_config or {})

    ctx = _current_trace()
    resolved_user = (user_id if user_id is not None else ctx.user_id) or None
    resolved_session = (session_id if session_id is not None else ctx.session_id) or None
    resolved_tags = list(tags or []) + list(ctx.tags)

    meta: dict[str, Any] = dict(metadata or {})
    if resolved_user:
        meta["langfuse_user_id"] = resolved_user
    if resolved_session:
        meta["langfuse_session_id"] = resolved_session
    if resolved_tags:
        meta["langfuse_tags"] = resolved_tags

    tracing_cfg: dict[str, Any] = {
        "callbacks": [_callback_handler()],
        "run_name": run_name,
    }
    if meta:
        tracing_cfg["metadata"] = meta

    return merge_langchain_config(base_config, tracing_cfg)


def merge_langchain_config(
    base: dict[str, Any] | None,
    extra: dict[str, Any] | None,
) -> dict[str, Any]:
    if not base and not extra:
        return {}
    if not base:
        return dict(extra or {})
    if not extra:
        return dict(base)

    merged: dict[str, Any] = dict(base)
    for key, value in extra.items():
        if key == "callbacks":
            merged_callbacks = list(merged.get("callbacks") or [])
            merged_callbacks.extend(value or [])
            merged["callbacks"] = merged_callbacks
        elif key == "metadata" and isinstance(value, dict):
            base_meta = merged.get("metadata")
            if isinstance(base_meta, dict):
                merged["metadata"] = {**base_meta, **value}
            else:
                merged["metadata"] = dict(value)
        elif key == "configurable" and isinstance(value, dict):
            base_cfg = merged.get("configurable")
            if isinstance(base_cfg, dict):
                merged["configurable"] = {**base_cfg, **value}
            else:
                merged["configurable"] = dict(value)
        else:
            merged[key] = value
    return merged


@contextmanager
def traced_llm_span(
    run_name: str,
    *,
    user_id: str | None = None,
    session_id: str | None = None,
    trace_name: str | None = None,
):
    """Parent span so Langfuse shows ``{trace_name} > {run_name}`` style hierarchy."""
    if not is_langfuse_enabled():
        yield None
        return

    configure_langfuse_env()
    from langfuse import get_client, propagate_attributes

    ctx = _current_trace()
    resolved_user = (user_id if user_id is not None else ctx.user_id) or None
    resolved_session = (session_id if session_id is not None else ctx.session_id) or None
    resolved_trace_name = trace_name or run_name

    langfuse = get_client()
    with langfuse.start_as_current_observation(as_type="span", name=run_name) as span:
        with propagate_attributes(
            trace_name=resolved_trace_name,
            user_id=resolved_user,
            session_id=resolved_session,
            tags=list(ctx.tags) or None,
        ):
            yield span


@asynccontextmanager
async def atraced_llm_span(
    run_name: str,
    *,
    user_id: str | None = None,
    session_id: str | None = None,
    trace_name: str | None = None,
):
    with traced_llm_span(
        run_name,
        user_id=user_id,
        session_id=session_id,
        trace_name=trace_name,
    ) as span:
        yield span


def final_answer_trace_input(system_prompt: str, user_prompt: str) -> dict[str, Any]:
    """ChatML-style payload for Langfuse (``create_agent`` hides system in model I/O)."""
    return {
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ]
    }


def record_final_answer_span_input(
    span: Any,
    *,
    system_prompt: str,
    user_prompt: str,
) -> None:
    """Attach full system + user prompts to the parent ``final answer`` span."""
    if span is None or not is_langfuse_enabled():
        return
    try:
        update = getattr(span, "update", None)
        if callable(update):
            update(input=final_answer_trace_input(system_prompt, user_prompt))
    except Exception as exc:
        logger.debug("Langfuse final-answer span input update skipped: {}", exc)


async def traced_ainvoke(
    runnable: Any,
    input: Any,
    *,
    run_name: str,
    config: dict[str, Any] | None = None,
    user_id: str | None = None,
    session_id: str | None = None,
    tags: list[str] | None = None,
    use_parent_span: bool = True,
    trace_name: str | None = None,
) -> Any:
    invoke_config = langchain_invoke_config(
        run_name,
        user_id=user_id,
        session_id=session_id,
        tags=tags,
        base_config=config,
    )
    if not use_parent_span or not is_langfuse_enabled():
        return await runnable.ainvoke(input, invoke_config or None)

    async with atraced_llm_span(
        run_name,
        user_id=user_id,
        session_id=session_id,
        trace_name=trace_name or run_name,
    ):
        return await runnable.ainvoke(input, invoke_config or None)


__all__ = [
    "LlmRunName",
    "LlmTraceContext",
    "allm_trace_context",
    "atraced_llm_span",
    "configure_langfuse_env",
    "final_answer_trace_input",
    "flush_langfuse",
    "is_langfuse_enabled",
    "langchain_invoke_config",
    "langfuse_base_url",
    "llm_trace_context",
    "merge_langchain_config",
    "record_final_answer_span_input",
    "traced_ainvoke",
    "traced_llm_span",
]
