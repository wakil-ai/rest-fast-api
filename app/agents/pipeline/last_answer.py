"""Final reasoning LLM: LangGraph thread memory + retrieval context in the prompt."""

from __future__ import annotations

from collections.abc import AsyncGenerator, Awaitable, Callable
from typing import Any

from langchain_core.prompts import PromptTemplate

from app.agents.base import BaseAgent
from app.agents.common.runtime import agent_session_thread_id
from app.agents.common.state import AgentState, GenerationContext
from app.agents.pipeline.schemas import (
    ChatPipelineState,
    ProgressEventType,
    normalize_assistant_name_for_registry,
)
from app.core.dependencies import get_prompt_registry
from app.core.logger import logger
from app.utils.streaming import format_progress_event

# Max characters per field in debug previews (avoid huge log lines).
_FINAL_LLM_DEBUG_PREVIEW: int = 4000


def _preview(text: str | None, limit: int = _FINAL_LLM_DEBUG_PREVIEW) -> str:
    if text is None:
        return ""
    s = str(text)
    if len(s) <= limit:
        return s
    return f"{s[:limit]}…<{len(s)} chars total>"


def _log_final_llm_inputs(
    *,
    state: ChatPipelineState,
    query: str,
    system_prompt: str,
    ctx: GenerationContext,
    stream: bool,
) -> None:
    """Log payloads sent to the final reasoning model (DEBUG level)."""
    rd = state.retrieval_docs or ""
    md = state.memory_docs or ""
    logger.debug(
        "[last_answer] Final LLM | session_id={} message_id={} user_id={} stream={} "
        "assistant_name={} selected_assistant={} intent={} court_route={} "
        "| user_query_chars={} system_prompt_chars={} retrieval_docs_chars={} "
        "memory_docs_chars={} gen_ctx.context_chars={} gen_ctx.chat_history_chars={} "
        "attachments_count={}",
        state.session_id,
        state.message_id,
        state.user_id,
        stream,
        ctx.assistant_name,
        state.selected_assistant,
        state.classified_legal_intent,
        state.court_route_tag,
        len(query),
        len(system_prompt),
        len(rd),
        len(md),
        len(ctx.context or ""),
        len(ctx.chat_history or ""),
        len(ctx.attachments or []),
    )
    logger.debug("[last_answer] Final LLM user_query (preview):\n{}", _preview(query))
    logger.debug(
        "[last_answer] Final LLM system_prompt (preview):\n{}", _preview(system_prompt)
    )
    logger.debug(
        "[last_answer] Final LLM retrieval_docs / ctx.context (preview):\n{}",
        _preview(ctx.context),
    )
    logger.debug(
        "[last_answer] Final LLM memory_docs / ctx.chat_history (preview):\n{}",
        _preview(ctx.chat_history),
    )


def _build_enhanced_query(state: ChatPipelineState) -> str:
    base_query = state.query
    if state.resolved_query and state.resolved_query != base_query:
        return (
            f"{base_query}\nResolved Query which may help you to clarify question: "
            f"{state.resolved_query}"
        )
    return base_query


def _format_context_chat_prompt(
    tmpl: Any,
    *,
    context: str,
    chat_history: str,
) -> str:
    """Format ``{context}`` / ``{chat_history}`` prompts after LangGraph state round-trips."""
    ctx = context or ""
    hist = chat_history or ""
    kwargs = {"context": ctx, "chat_history": hist}

    if tmpl is None:
        raise ValueError("prompt template is None")

    if isinstance(tmpl, PromptTemplate):
        return tmpl.format(**kwargs)

    if isinstance(tmpl, str):
        return tmpl.format(**kwargs)

    if isinstance(tmpl, dict):
        # LangGraph / JSON round-trip may wrap LangChain objects as {"kwargs": {"template": ...}}
        inner = tmpl.get("kwargs") if isinstance(tmpl.get("kwargs"), dict) else tmpl
        raw = inner.get("template") if isinstance(inner, dict) else None
        if isinstance(raw, str):
            try:
                return raw.format(**kwargs)
            except KeyError:
                pass
        try:
            reconstructed = PromptTemplate.model_validate(
                inner if isinstance(inner, dict) else tmpl
            )
            return reconstructed.format(**kwargs)
        except Exception as e:
            logger.warning(
                "[last_answer] Could not coerce dict to PromptTemplate: %s; keys=%s",
                e,
                list(tmpl.keys())[:12],
            )
        if isinstance(raw, str):
            return raw.format(**kwargs)

    fmt = getattr(tmpl, "format", None)
    if callable(fmt) and not isinstance(tmpl, dict):
        return fmt(**kwargs)

    raise TypeError(
        f"Unsupported prompt template type {type(tmpl)!r}; expected PromptTemplate or str"
    )


def _append_project_instructions(base_prompt: str, state: ChatPipelineState) -> str:
    instructions = (state.project_instructions or "").strip()
    if not instructions:
        return base_prompt
    return (
        f"{base_prompt.rstrip()}\n\n"
        "## Project-specific instructions (user-defined)\n"
        f"{instructions}"
    )


def _build_system_prompt(state: ChatPipelineState) -> str:
    registry = get_prompt_registry()
    ctx_kw = {
        "context": state.retrieval_docs or "",
        "chat_history": "",
    }
    if state.answer_prompt_template is not None:
        base = _format_context_chat_prompt(state.answer_prompt_template, **ctx_kw)
    else:
        assistant = normalize_assistant_name_for_registry(
            state.selected_assistant or "main"
        )
        prompt_template = registry.get_assistant_prompt(assistant)
        base = _format_context_chat_prompt(prompt_template, **ctx_kw)
    return _append_project_instructions(base, state)


def build_generation_context_for_result(state: ChatPipelineState) -> GenerationContext:
    """Snapshot context for AgentRunResult after retrieval + generation."""
    return _create_generation_context(state, _build_system_prompt(state))


def _create_generation_context(
    state: ChatPipelineState, system_prompt: str
) -> GenerationContext:
    assistant = normalize_assistant_name_for_registry(
        state.selected_assistant or "main"
    )
    tid = (
        agent_session_thread_id(state.user_id, state.session_id)
        if state.session_id
        else None
    )
    return GenerationContext(
        context=state.retrieval_docs or "",
        system_prompt=system_prompt,
        user_id=state.user_id,
        assistant_name=str(assistant),
        chat_history="",
        attachments=list(state.attachments or []),
        classified_legal_intent=state.classified_legal_intent,
        court_route_tag=state.court_route_tag,
        langgraph_thread_id=tid,
    )


async def run_last_answer(
    state: ChatPipelineState,
    *,
    generation_agent: BaseAgent,
    attach_agent: BaseAgent,
    progress_callback: Callable[[dict[str, Any]], Awaitable[None]] | None,
    stream: bool,
) -> str:
    async def emit(
        event_type: str, status: str, message: str, details: dict | None = None
    ) -> None:
        if progress_callback:
            event = await format_progress_event(event_type, status, message, details)
            await progress_callback(event)

    await emit(
        ProgressEventType.ANSWER_GENERATION,
        "in_progress",
        "Generating final answer...",
    )
    query = _build_enhanced_query(state)
    system_prompt = _build_system_prompt(state)
    ctx = _create_generation_context(state, system_prompt)
    _log_final_llm_inputs(
        state=state,
        query=query,
        system_prompt=system_prompt,
        ctx=ctx,
        stream=stream,
    )
    try:
        response = await generation_agent.generate_from_context(
            query=query, ctx=ctx, stream=stream
        )
        if stream:
            return await _consume_stream(
                response, state, attach_agent, progress_callback, emit
            )
        answer, meta = _unwrap_non_streaming(response)
        state.answer = answer
        state.generation_meta = meta
        state.attachments = meta.get("attachments") or []
        st = _minimal_agent_state(state, attach_agent)
        attachments = await attach_agent.attach_outputs(answer, st)
        merged_meta = dict(meta or {})
        merged_meta["attachments"] = attachments if attachments else None
        state.generation_meta = merged_meta
        state.attachments = attachments
        await emit(ProgressEventType.ANSWER_GENERATION, "completed", "Answer ready")
        return answer
    except Exception as error:
        logger.error(f"Answer generation failed: {error}", exc_info=True)
        state.answer = "Error generating answer. Please try again."
        await emit(
            ProgressEventType.ANSWER_GENERATION,
            "failed",
            f"Answer generation failed: {str(error)}",
        )
        return state.answer


def _minimal_agent_state(state: ChatPipelineState, agent: BaseAgent) -> AgentState:
    from app.agents.common.state import AgentRequestContext

    req = AgentRequestContext(
        user_id=state.user_id,
        session_id=state.session_id,
        message_id=state.message_id,
        query=state.query,
        assistant=normalize_assistant_name_for_registry(
            state.selected_assistant or agent.assistant_name
        ),
        file_ids=state.file_ids,
        project_id=state.project_id,
        stream=False,
    )
    st = AgentState(
        request=req,
        resolved_assistant=normalize_assistant_name_for_registry(
            state.selected_assistant or agent.assistant_name
        ),
        attachments=state.attachments,
    )
    st.classified_legal_intent = state.classified_legal_intent
    st.court_route_tag = state.court_route_tag
    return st


def _unwrap_non_streaming(response: Any) -> tuple[str, dict[str, Any]]:
    if isinstance(response, tuple):
        answer, meta = response
        return answer, meta if isinstance(meta, dict) else {}
    return str(response), {}


async def _consume_stream(
    response: Any,
    state: ChatPipelineState,
    attach_agent: BaseAgent,
    progress_callback: Callable[[dict[str, Any]], Awaitable[None]] | None,
    emit,
) -> str:
    full_answer = ""
    async for chunk in response:
        if isinstance(chunk, dict):
            if chunk.get("type") == "attachments":
                state.attachments = chunk.get("attachments") or []
                if progress_callback:
                    await progress_callback(
                        {"type": "attachments", "attachments": state.attachments}
                    )
            elif chunk.get("type") == "think":
                if progress_callback:
                    await progress_callback(chunk)
            elif chunk.get("type") == "_generation_meta":
                state.generation_meta = (
                    chunk.get("meta") if isinstance(chunk.get("meta"), dict) else {}
                ) or {}
            continue
        if isinstance(chunk, str):
            full_answer += chunk
            await emit("chunk", "in_progress", chunk)
    state.answer = full_answer
    st = _minimal_agent_state(state, attach_agent)
    attachments = await attach_agent.attach_outputs(full_answer, st)
    merged = dict(state.generation_meta or {})
    merged["attachments"] = attachments if attachments else None
    state.generation_meta = merged
    state.attachments = attachments
    await emit(ProgressEventType.ANSWER_GENERATION, "completed", "Answer ready")
    return full_answer


async def astream_last_answer(
    state: ChatPipelineState,
    *,
    generation_agent: BaseAgent,
    attach_agent: BaseAgent,
) -> AsyncGenerator[str | dict[str, Any], None]:
    """Yield answer chunks and metadata dicts (streaming)."""
    query = _build_enhanced_query(state)
    system_prompt = _build_system_prompt(state)
    ctx = _create_generation_context(state, system_prompt)
    _log_final_llm_inputs(
        state=state,
        query=query,
        system_prompt=system_prompt,
        ctx=ctx,
        stream=True,
    )
    response = await generation_agent.generate_from_context(
        query=query, ctx=ctx, stream=True
    )
    chunks: list[str] = []
    async for chunk in response:
        if isinstance(chunk, str):
            chunks.append(chunk)
        if isinstance(chunk, dict) and chunk.get("type") == "_generation_meta":
            state.generation_meta = (
                chunk.get("meta") if isinstance(chunk.get("meta"), dict) else {}
            ) or {}
        yield chunk
    full = "".join(chunks)
    state.answer = full
    st = _minimal_agent_state(state, attach_agent)
    attachments = await attach_agent.attach_outputs(full, st)
    merged = dict(state.generation_meta or {})
    merged["attachments"] = attachments if attachments else None
    state.generation_meta = merged
    state.attachments = attachments
    if attachments:
        yield {"type": "attachments", "attachments": attachments}
    yield {
        "type": "_generation_meta",
        "meta": merged,
        "resolved_assistant": normalize_assistant_name_for_registry(
            state.selected_assistant or attach_agent.assistant_name
        ),
    }
