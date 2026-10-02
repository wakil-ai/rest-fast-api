"""handle_chat_ask resolves inference_tier from the same credit check it
already performs, and forwards the identical value to both the streaming and
non-streaming orchestration paths. No new subscription lookup is involved:
these tests mock verify_user_credits directly, the same way the tier is
resolved in production -- from refund_info, not a second DB call.
"""

from unittest.mock import AsyncMock, MagicMock

import pytest

from models.chat import ChatRequest
from services.chat_service import ChatService


def _service(refund_info):
    service = ChatService.__new__(ChatService)
    service.verify_user_credits = AsyncMock(return_value=refund_info)
    service.extract_assistant_config = MagicMock(return_value=(1, "main_collection"))
    service.prepare_chat_request = AsyncMock(
        return_value=("session-1", "message-1", None)
    )
    service.is_dt_team_request = MagicMock(return_value=False)
    service.run_orchestrated_chat = AsyncMock(return_value=("answer", {}))
    service.astream_orchestrated_chat = MagicMock()
    service.append_dt_team_disclaimer = MagicMock(side_effect=lambda answer, **_: answer)
    service.upload_final_answer_docx = AsyncMock(return_value=[])
    service.build_message_metadata = MagicMock(return_value={})
    service.schedule_message_persistence = MagicMock()
    return service


def _request(**overrides) -> ChatRequest:
    fields = {
        "user_id": "user-1",
        "session_id": "session-1",
        "query": "What is a contract?",
        "assistant": "main",
    }
    fields.update(overrides)
    return ChatRequest(**fields)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "refund_info,expected_tier",
    [
        ({"kind": "pool"}, "paid"),
        ({"kind": "daily_pass", "consumed": {}}, "paid"),
        ({"kind": "signup_bonus"}, "free"),
        ({"kind": "daily_promo"}, "free"),
        (None, "free"),
    ],
)
async def test_non_streaming_request_forwards_resolved_tier(
    refund_info, expected_tier,
):
    service = _service(refund_info)
    request = _request(stream=False)

    await service.handle_chat_ask(request, raw_request=MagicMock())

    kwargs = service.run_orchestrated_chat.await_args.kwargs
    assert kwargs["inference_tier"] == expected_tier
    service.astream_orchestrated_chat.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "refund_info,expected_tier",
    [
        ({"kind": "pool"}, "paid"),
        ({"kind": "daily_pass", "consumed": {}}, "paid"),
        ({"kind": "signup_bonus"}, "free"),
        ({"kind": "daily_promo"}, "free"),
        (None, "free"),
    ],
)
async def test_streaming_request_forwards_same_resolved_tier(
    refund_info, expected_tier,
):
    service = _service(refund_info)
    request = _request(stream=True)

    service.create_streaming_response = MagicMock(side_effect=lambda gen: gen)
    await service.handle_chat_ask(request, raw_request=MagicMock())

    kwargs = service.astream_orchestrated_chat.call_args.kwargs
    assert kwargs["inference_tier"] == expected_tier
    service.run_orchestrated_chat.assert_not_called()


@pytest.mark.asyncio
async def test_streaming_and_non_streaming_agree_for_the_same_subscription_state():
    """Both dispatch paths must derive the identical tier from the identical
    refund_info -- a per-path divergence here would route the same user to
    two different model providers depending only on whether they streamed."""
    refund_info = {"kind": "pool"}

    non_stream_service = _service(refund_info)
    await non_stream_service.handle_chat_ask(
        _request(stream=False), raw_request=MagicMock()
    )

    stream_service = _service(refund_info)
    stream_service.create_streaming_response = MagicMock(side_effect=lambda gen: gen)
    await stream_service.handle_chat_ask(_request(stream=True), raw_request=MagicMock())

    assert (
        non_stream_service.run_orchestrated_chat.await_args.kwargs["inference_tier"]
        == stream_service.astream_orchestrated_chat.call_args.kwargs["inference_tier"]
        == "paid"
    )
