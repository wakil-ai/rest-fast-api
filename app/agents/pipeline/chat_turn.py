"""Orchestrate retrieval_agent then last_agent for /chat/ask and agentic flows."""

from __future__ import annotations

from collections.abc import AsyncGenerator, Awaitable, Callable
from typing import Any

from app.agents.base import BaseAgent
from app.agents.common.state import AgentRequestContext, AgentRunResult
from app.agents.main import MainAgent
from app.agents.pipeline.last_answer import (
    astream_last_answer,
    build_generation_context_for_result,
    run_last_answer,
)
from app.agents.pipeline.retrieval_runner import ContextRetrievalRunner
from app.core.assistants import AssistantConfig
from app.core.dependencies import get_chat_history_service, get_project_service
from app.agents.pipeline.schemas import (
    ChatPipelineState,
    normalize_assistant_name_for_registry,
)


async def _resolve_pipeline_project_id(state: ChatPipelineState) -> None:
    if state.project_id or not state.session_id:
        return
    session = await get_chat_history_service().get_session(state.session_id)
    project_id = session.get("project_id") if session else None
    if project_id:
        state.project_id = str(project_id)


async def _resolve_pipeline_project_instructions(state: ChatPipelineState) -> None:
    if not state.project_id or state.project_instructions:
        return
    try:
        project = await get_project_service().get_project(
            state.project_id, state.user_id
        )
    except Exception:
        return
    instructions = get_project_service().extract_instructions(project)
    if instructions:
        state.project_instructions = instructions


def _pipeline_state_from_request(request: AgentRequestContext) -> ChatPipelineState:
    requested = str(request.assistant or "main").strip()
    canonical = AssistantConfig.validate_assistant_or_default(request.assistant)
    return ChatPipelineState(
        query=request.query,
        user_id=request.user_id,
        session_id=request.session_id,
        message_id=request.message_id,
        project_id=request.project_id,
        file_ids=request.file_ids,
        locked_assistant=canonical,
        requested_assistant=requested,
        stream=request.stream,
    )


def get_generation_agent_for_pipeline(state: ChatPipelineState) -> BaseAgent:
    """Generation LLM: always MainAgent; assistant prompts come from retrieval state."""
    _ = state
    return MainAgent()


def get_attach_agent_for_pipeline(
    state: ChatPipelineState, get_agent: Callable[[str], BaseAgent]
) -> BaseAgent:
    key = normalize_assistant_name_for_registry(state.selected_assistant or "main")
    return get_agent(key)


async def run_two_stage_chat(
    request: AgentRequestContext,
    get_agent: Callable[[str], BaseAgent],
    *,
    progress_callback: Callable[[dict[str, Any]], Awaitable[None]] | None = None,
) -> AgentRunResult:
    state = _pipeline_state_from_request(request)
    await _resolve_pipeline_project_id(state)
    await _resolve_pipeline_project_instructions(state)
    runner = ContextRetrievalRunner(progress_callback=progress_callback)
    await runner.run(state, get_agent)

    gen_agent = get_generation_agent_for_pipeline(state)
    attach_agent = get_attach_agent_for_pipeline(state, get_agent)

    await run_last_answer(
        state,
        generation_agent=gen_agent,
        attach_agent=attach_agent,
        progress_callback=progress_callback,
        stream=False,
    )

    meta = dict(state.generation_meta or {})
    meta["workflow"] = "two_stage_pipeline"
    if state.selected_assistant:
        meta["selected_assistant"] = state.selected_assistant
    if state.web_search_output:
        meta["used_web_search"] = bool(state.web_search_output.get("docs"))

    resolved = normalize_assistant_name_for_registry(
        state.selected_assistant or request.assistant
    )
    gc = build_generation_context_for_result(state)
    return AgentRunResult(
        answer=state.answer or "",
        resolved_assistant=resolved,
        metadata=meta,
        attachments=state.attachments,
        generation_context=gc,
    )


async def astream_two_stage_chat(
    request: AgentRequestContext,
    get_agent: Callable[[str], BaseAgent],
) -> AsyncGenerator[str | dict[str, Any], None]:
    state = _pipeline_state_from_request(request)
    await _resolve_pipeline_project_id(state)
    await _resolve_pipeline_project_instructions(state)
    runner = ContextRetrievalRunner(progress_callback=None)
    await runner.run(state, get_agent)

    gen_agent = get_generation_agent_for_pipeline(state)
    attach_agent = get_attach_agent_for_pipeline(state, get_agent)

    async for item in astream_last_answer(
        state,
        generation_agent=gen_agent,
        attach_agent=attach_agent,
    ):
        yield item
