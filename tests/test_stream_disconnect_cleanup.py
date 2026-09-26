"""A client disconnect must still refund an unanswered charge and tell the LLM
service to stop, driven through the real Starlette disconnect path (uvicorn
advertises ASGI spec 2.3, so Starlette cancels the response task group).
"""

import asyncio
import time
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from core.config import settings
from services.chat_service import ChatService
from services.llm_service_client import LlmServiceClient

CANCEL_URL = "http://llm.internal/api/v1/chat/generations/cancel"


class _Response:
    status_code = 200
    is_success = True
    text = "{}"

    def json(self):
        return {}


class _HangingStream:
    is_success = True

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return None

    async def aiter_lines(self):
        yield ""
        await asyncio.Event().wait()


def _fake_async_client(cancel_calls: list, *, cancel_hangs: bool):
    class FakeAsyncClient:
        def __init__(self, *args, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return None

        def stream(self, method, url, **kwargs):
            return _HangingStream()

        async def request(self, method, url, **kwargs):
            if cancel_hangs:
                await asyncio.Event().wait()
            await asyncio.sleep(0.02)
            cancel_calls.append((method, url, kwargs.get("json")))
            return _Response()

    return FakeAsyncClient


async def _until(predicate, timeout: float = 2.0) -> None:
    deadline = time.monotonic() + timeout
    while not predicate() and time.monotonic() < deadline:
        await asyncio.sleep(0.01)


async def _disconnect_mid_stream(monkeypatch, *, cancel_hangs: bool):
    monkeypatch.setattr(settings, "LLM_SERVICE_URL", "http://llm.internal")
    cancel_calls: list = []
    refunds: list = []
    monkeypatch.setattr(
        "services.llm_service_client.httpx.AsyncClient",
        _fake_async_client(cancel_calls, cancel_hangs=cancel_hangs),
    )

    service = ChatService.__new__(ChatService)
    service.build_llm_inference_payload = AsyncMock(
        return_value={"generation_id": "message-1"}
    )
    service.rate_limit_service = MagicMock()

    async def refund_credits(*args):
        await asyncio.sleep(0.01)
        refunds.append(args)

    service.rate_limit_service.refund_credits = refund_credits

    async def receive():
        await asyncio.sleep(0.1)
        return {"type": "http.disconnect"}

    async def send(message):
        return None

    started = time.monotonic()
    with patch(
        "services.chat_service.get_llm_service_client",
        return_value=LlmServiceClient(),
    ):
        response = ChatService.create_streaming_response(
            service.astream_orchestrated_chat(
                user_id="user-1",
                session_id="session-1",
                message_id="message-1",
                query="question",
                file_ids=None,
                assistant="main",
                started_at=0.0,
                credit_cost=3,
                refund_info={"kind": "daily_promo"},
            )
        )
        scope = {"type": "http", "asgi": {"version": "3.0", "spec_version": "2.3"}}
        await response(scope, receive, send)
        await _until(lambda: refunds and (cancel_hangs or cancel_calls))
    return refunds, cancel_calls, time.monotonic() - started


@pytest.mark.asyncio
async def test_disconnect_refunds_and_notifies_llm_service(monkeypatch):
    refunds, cancel_calls, _ = await _disconnect_mid_stream(
        monkeypatch, cancel_hangs=False
    )

    assert refunds == [("user-1", 3, {"kind": "daily_promo"})]
    assert cancel_calls == [("POST", CANCEL_URL, {"generation_id": "message-1"})]
