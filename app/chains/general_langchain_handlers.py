"""Shared helpers for HTTP layers that run the LangGraph general assistant."""

from collections.abc import AsyncGenerator
from time import perf_counter
from typing import Any

from app.chains.general_langchain_agent import astream_general_lc_agent
from app.core.dependencies import get_chat_chain, get_chat_service


async def collect_lc_file_context(
    *,
    user_id: str,
    query: str,
    file_ids: list[str] | None,
    project_id: str | None = None,
) -> str:
    chain = get_chat_chain()
    return await chain._collect_file_context(
        file_ids, user_id, query, project_id=project_id
    )


async def astream_lc_with_persistence(
    *,
    thread_id: str,
    query: str,
    file_context: str,
    user_id: str,
    session_id: str,
    message_id: str,
    file_ids: list[str] | None,
    assistant: str,
    started_at: float,
    dt_team_disclaimer_suffix: str = "",
    project_id: str | None = None,
) -> AsyncGenerator[Any, None]:
    chat_service = get_chat_service()
    answer_chunks: list[str] = []
    generation_meta: dict[str, Any] = {}

    yield {"type": "metadata", "session_id": session_id, "message_id": message_id}

    lc_stream = astream_general_lc_agent(
        thread_id=thread_id,
        query=query,
        file_context=file_context,
    )
    async for item in lc_stream:
        if isinstance(item, dict) and item.get("type") == "_generation_meta":
            generation_meta = (item.get("meta") or {}) if isinstance(
                item.get("meta"), dict
            ) else {}
            continue
        if isinstance(item, str):
            answer_chunks.append(item)
            yield item
        elif item is not None:
            yield item

    latency_ms = int((perf_counter() - started_at) * 1000)
    merged_meta = dict(generation_meta)
    merged_meta["latency_ms"] = latency_ms

    if dt_team_disclaimer_suffix:
        answer_chunks.append(dt_team_disclaimer_suffix)
        yield dt_team_disclaimer_suffix

    metadata = chat_service.build_message_metadata(
        assistant=assistant,
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
