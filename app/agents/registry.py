from __future__ import annotations

from app.agents.common.state import AgentRequestContext, AgentRunResult
from app.core.dependencies import get_chat_orchestrator


async def invoke_chat_agent_run(
    *,
    user_id: str,
    session_id: str,
    message_id: str,
    query: str,
    file_ids: list[str] | None,
    assistant: str,
    project_id: str | None,
) -> tuple[str, dict, object]:
    request = AgentRequestContext(
        user_id=user_id,
        session_id=session_id,
        message_id=message_id,
        query=query,
        assistant=assistant,
        file_ids=file_ids,
        project_id=project_id,
        stream=False,
    )
    result: AgentRunResult = await get_chat_orchestrator().invoke(request)
    return result.answer, result.metadata, result.generation_context
