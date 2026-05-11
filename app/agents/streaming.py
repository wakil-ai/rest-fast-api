"""Streaming helpers for chat agents."""

from collections.abc import AsyncGenerator
from time import perf_counter
from typing import Any

from app.agents.common.state import AgentRequestContext
from app.core.dependencies import get_chat_orchestrator, get_chat_service


async def astream_chat_with_persistence(
    *,
    user_id: str,
    session_id: str,
    message_id: str,
    query: str,
    file_ids: list[str] | None,
    assistant: str,
    started_at: float,
    dt_team_disclaimer_suffix: str = "",
    project_id: str | None = None,
) -> AsyncGenerator[Any, None]:
    chat_service = get_chat_service()
    answer_chunks: list[str] = []
    generation_meta: dict[str, Any] = {}
    resolved_assistant = assistant
    persisted = False

    def finalize_persistence() -> None:
        nonlocal persisted
        if persisted:
            return
        persisted = True

        latency_ms = int((perf_counter() - started_at) * 1000)
        merged_meta = dict(generation_meta)
        merged_meta["latency_ms"] = latency_ms

        metadata = chat_service.build_message_metadata(
            assistant=resolved_assistant,
            stream=True,
            latency_ms=latency_ms,
            generation_meta=merged_meta,
        )
        chat_service.schedule_message_persistence(
            user_id=user_id,
            session_id=session_id,
            message_id=message_id,
            query=query,
            answer="".join(answer_chunks),
            file_ids=file_ids,
            metadata=metadata,
            project_id=project_id,
        )

    yield {"type": "metadata", "session_id": session_id, "message_id": message_id}

    request = AgentRequestContext(
        user_id=user_id,
        session_id=session_id,
        message_id=message_id,
        query=query,
        assistant=assistant,
        file_ids=file_ids,
        project_id=project_id,
        stream=True,
    )
    try:
        async for item in get_chat_orchestrator().astream(request):
            if isinstance(item, dict) and item.get("type") == "_generation_meta":
                generation_meta = (item.get("meta") or {}) if isinstance(
                    item.get("meta"), dict
                ) else {}
                resolved_assistant = str(item.get("resolved_assistant") or assistant)
                continue
            if isinstance(item, str):
                answer_chunks.append(item)
                yield item
            elif item is not None:
                if (
                    isinstance(item, dict)
                    and item.get("type") == "attachments"
                    and item.get("attachments")
                ):
                    generation_meta["attachments"] = item.get("attachments")
                if (
                    isinstance(item, dict)
                    and item.get("type") == "chunk"
                    and isinstance(item.get("chunk"), str)
                ):
                    answer_chunks.append(item["chunk"])
                yield item

        if dt_team_disclaimer_suffix:
            answer_chunks.append(dt_team_disclaimer_suffix)
            finalize_persistence()
            yield dt_team_disclaimer_suffix
        else:
            finalize_persistence()
    finally:
        finalize_persistence()
