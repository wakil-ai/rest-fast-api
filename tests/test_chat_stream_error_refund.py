"""An upstream ``error`` event refunds the upfront charge only when the user
saw no answer text -- neither standard ``chunk`` events nor progress-chunk text.
"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from services.chat_service import ChatService

COST = 3
REFUND_INFO = {"kind": "daily_promo"}
ERROR = {"type": "error", "error": "boom", "message": "boom"}


async def _run_stream(events: list[dict]) -> tuple[list, ChatService]:
    service = ChatService.__new__(ChatService)
    service.build_llm_inference_payload = AsyncMock(return_value={})
    service.rate_limit_service = MagicMock()
    service.rate_limit_service.refund_credits = AsyncMock()

    async def fake_stream_json(path, payload):
        for event in events:
            yield event

    llm_client = MagicMock()
    llm_client.stream_json = fake_stream_json

    with patch("services.chat_service.get_llm_service_client", return_value=llm_client):
        out = [
            item
            async for item in service.astream_orchestrated_chat(
                user_id="user-1",
                session_id="session-1",
                message_id="message-1",
                query="question",
                file_ids=None,
                assistant="main",
                started_at=0.0,
                credit_cost=COST,
                refund_info=REFUND_INFO,
            )
        ]
    return out, service


@pytest.mark.asyncio
async def test_error_before_any_text_refunds():
    out, service = await _run_stream([dict(ERROR)])

    service.rate_limit_service.refund_credits.assert_awaited_once_with(
        "user-1", COST, REFUND_INFO
    )
    assert out[-1] == ERROR


@pytest.mark.asyncio
async def test_error_after_progress_text_does_not_refund():
    progress_text = {"type": "progress", "event_type": "chunk", "message": "partial"}

    out, service = await _run_stream([progress_text, dict(ERROR)])

    service.rate_limit_service.refund_credits.assert_not_awaited()
    assert out[-2:] == [progress_text, ERROR]


@pytest.mark.asyncio
async def test_error_after_standard_chunk_does_not_refund():
    out, service = await _run_stream(
        [{"type": "chunk", "chunk": "partial"}, dict(ERROR)]
    )

    service.rate_limit_service.refund_credits.assert_not_awaited()
    assert out[-1] == ERROR


@pytest.mark.asyncio
async def test_stream_sends_message_id_as_generation_id():
    _, service = await _run_stream([dict(ERROR)])

    kwargs = service.build_llm_inference_payload.await_args.kwargs
    assert kwargs["generation_id"] == "message-1"
