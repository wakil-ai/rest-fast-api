import asyncio
import json
from unittest.mock import AsyncMock

from fastapi.testclient import TestClient

from app.core.config import settings
from app.main import create_app
from app.services.chat_service import ChatService


class _FakeChatService:
    def validate_query_length(self, query: str) -> None:  # noqa: ARG002
        return None

    def extract_assistant_config(self, assistant_name: str):  # noqa: ARG002
        return 1, "collection"

    async def verify_user_credits(self, **kwargs):  # noqa: ANN003
        return None

    async def ask_question(self, **kwargs):  # noqa: ANN003
        return "OK"

    def create_streaming_response(self, generator) -> object:  # noqa: ANN001,ARG002
        raise AssertionError("create_streaming_response must not be called")


class _StreamingFakeChatService(_FakeChatService):
    def create_streaming_response(self, generator) -> object:  # noqa: ANN001
        return ChatService.create_streaming_response(generator)


def test_stream_false_returns_json(monkeypatch):
    app = create_app()

    # Patch the module-level singleton used by the router.
    import app.api.v2.chat as chat_module

    fake = _FakeChatService()
    fake.ask_question = AsyncMock(return_value="OK")
    monkeypatch.setattr(chat_module, "chat_service", fake)

    client = TestClient(app)
    headers = {settings.API_KEY_NAME.lower(): settings.API_KEY}

    resp = client.post(
        f"{settings.API_PREFIX}/chat/ask",
        headers=headers,
        json={
            "user_id": "u1",
            "query": "hello",
            "stream": False,
            "assistant": "main",
        },
    )

    assert resp.status_code == 200
    assert resp.headers.get("content-type", "").startswith("application/json")
    assert resp.json()["answer"] == "OK"


def test_stream_true_emits_keepalive_before_answer(monkeypatch):
    app = create_app()

    import app.api.v2.chat as chat_module
    import app.utils.streaming as streaming_module

    async def delayed_stream(**kwargs):  # noqa: ANN003
        await asyncio.sleep(0.03)

        async def response_gen():
            yield "OK"

        return response_gen()

    fake = _StreamingFakeChatService()
    fake.ask_question = AsyncMock(side_effect=delayed_stream)
    monkeypatch.setattr(chat_module, "chat_service", fake)
    monkeypatch.setattr(
        streaming_module.settings,
        "STREAM_KEEPALIVE_INTERVAL_SECONDS",
        0.01,
    )

    client = TestClient(app)
    headers = {settings.API_KEY_NAME.lower(): settings.API_KEY}

    with client.stream(
        "POST",
        f"{settings.API_PREFIX}/chat/ask",
        headers=headers,
        json={
            "user_id": "u1",
            "query": "hello",
            "stream": True,
            "assistant": "main",
        },
    ) as resp:
        assert resp.status_code == 200
        assert resp.headers.get("content-type", "").startswith("text/event-stream")

        saw_keepalive = False
        saw_chunk = False

        for line in resp.iter_lines():
            if not line or not line.startswith("data: "):
                continue

            payload = json.loads(line.removeprefix("data: "))
            if payload == {"type": "progress", "message": "still working"}:
                saw_keepalive = True
            if payload == {"type": "chunk", "chunk": "OK"}:
                saw_chunk = True
                break

        assert saw_keepalive is True
        assert saw_chunk is True
