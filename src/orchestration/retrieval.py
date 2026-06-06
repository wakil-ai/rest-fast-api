from __future__ import annotations

from dataclasses import replace
from typing import Any

from src.core.dependencies import get_prompt_registry

from src.orchestration.utils import AgentRequestContext, get_chat_agent
from src.assistants.court import CourtAgent
from src.core.assistants import AssistantConfig
from src.models.retrieval_models import RetrievalResult
from src.orchestration.state import COLLECTION_KEYS, RetrievalRewriteState
from src.orchestration.text import history_text_from_state

COURT_SPECIALISTS = frozenset(
    {
        "administrative_court",
        "civil_court",
        "criminal_court",
        "economic_court",
    }
)


def resolve_collection_name(collection_key: str | None) -> str:
    key = (collection_key or "customs").strip()
    if key in COLLECTION_KEYS:
        return COLLECTION_KEYS[key]
    return key


def resolve_assistant(state: RetrievalRewriteState) -> str:
    raw = (
        state.get("selected_assistant")
        or state.get("assistant_name")
        or state.get("collection_name")
        or "main"
    )
    return AssistantConfig.validate_assistant_or_default(str(raw))


def request_from_state(state: RetrievalRewriteState) -> AgentRequestContext:
    return AgentRequestContext(
        user_id=state.get("user_id", ""),
        session_id=state.get("session_id", ""),
        message_id=state.get("message_id", ""),
        query=state["query"],
        assistant=resolve_assistant(state),
        file_ids=state.get("file_ids"),
        project_id=state.get("project_id"),
        stream=False,
        preloaded_file_context=state.get("file_context"),
    )


def get_agent(assistant_name: str | None):
    return get_chat_agent(assistant_name)


def _updates_from_result(result: RetrievalResult) -> dict[str, Any]:
    updates: dict[str, Any] = {
        "retrieval_context": result.context or "",
    }
    if result.prompt_template is not None:
        updates["answer_prompt_template"] = result.prompt_template
    if result.classified_legal_intent:
        updates["classified_legal_intent"] = result.classified_legal_intent
    if result.attachments:
        updates["attachments"] = list(result.attachments)
    return updates


async def retrieve_for_assistant(
    state: RetrievalRewriteState,
    *,
    upload_context: str = "",
) -> dict[str, Any]:
    assistant = resolve_assistant(state)
    query = state.get("rewritten_query") or state["query"]
    chat_history = history_text_from_state(state)
    file_context = upload_context or state.get("file_context") or ""

    if assistant == "court":
        return await _retrieve_court_routed(state, file_context=file_context)

    if assistant == "tax":
        result = await get_agent("tax").retrieve(
            query=query,
            file_context=file_context,
            chat_history=chat_history,
        )
        return _updates_from_result(result)

    if assistant == "contract_analyzer":
        result = await get_agent("contract_analyzer").retrieve(
            query=query,
            file_context=file_context,
            chat_history=chat_history,
        )
        return _updates_from_result(result)

    if assistant in COURT_SPECIALISTS:
        agent = get_agent(assistant)
        kwargs: dict[str, Any] = {}
        if assistant == "administrative_court":
            kwargs["court_route_tag"] = (
                state.get("court_route_tag")
                or getattr(agent, "DEFAULT_COURT_ROUTE_TAG", None)
            )
            if hasattr(agent, "_forced_court_route_tag"):
                agent._forced_court_route_tag = kwargs["court_route_tag"]
        try:
            result = await agent.retrieve(
                query=query,
                file_context=file_context,
                chat_history=chat_history,
                **kwargs,
            )
        finally:
            if hasattr(agent, "_forced_court_route_tag"):
                agent._forced_court_route_tag = None

        return _updates_from_result(result)

    result = await get_agent("main").retrieve(
        query=query,
        file_context=file_context,
        chat_history=chat_history,
    )
    return _updates_from_result(result)


async def _retrieve_court_routed(
    state: RetrievalRewriteState,
    *,
    file_context: str,
) -> dict[str, Any]:
    request = request_from_state(state)
    if file_context:
        request = replace(request, preloaded_file_context=file_context)

    court = CourtAgent()
    sub_agent, routed_request, court_route_tag = await court._route(request)
    query = state.get("rewritten_query") or state["query"]

    if hasattr(sub_agent, "_forced_court_route_tag"):
        sub_agent._forced_court_route_tag = court_route_tag
    try:
        result = await sub_agent.retrieve(
            query=query,
            file_context=routed_request.preloaded_file_context or "",
            chat_history="",
            court_route_tag=court_route_tag,
        )
    finally:
        if hasattr(sub_agent, "_forced_court_route_tag"):
            sub_agent._forced_court_route_tag = None

    updates = _updates_from_result(result)
    updates["selected_assistant"] = routed_request.assistant
    updates["court_route_tag"] = court_route_tag
    return updates
