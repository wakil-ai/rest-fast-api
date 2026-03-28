import asyncio
import json
from unittest.mock import AsyncMock

from fastapi.testclient import TestClient

from app.core.config import settings
from app.main import create_app
from app.services.chat_service import ChatService


class _FakeChatService:
    async def prepare_chat_request(self, user_id: str, session_id: str):  # noqa: ARG002
        return session_id, "msg_test"

    def validate_query_length(self, query: str) -> None:  # noqa: ARG002
        return None

    def extract_assistant_config(self, assistant_name: str):  # noqa: ARG002
        return 1, "collection"

    async def verify_user_credits(self, **kwargs):  # noqa: ANN003
        return None

    async def ask_question(self, **kwargs):  # noqa: ANN003
        return "OK"

    def build_agentic_state(self, request, message_id: str):  # noqa: ANN001
        return {
            "query": request.query,
            "user_id": request.user_id,
            "session_id": request.session_id,
            "message_id": message_id,
            "file_ids": request.file_ids,
            "project_id": request.project_id,
        }

    def build_message_metadata(self, **kwargs):  # noqa: ANN003
        return {"assistant": kwargs["assistant"], "latency_ms": kwargs["latency_ms"]}

    def schedule_message_persistence(self, **kwargs):  # noqa: ANN003
        return None

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
            "session_id": "ses_test",
            "query": "hello",
            "stream": False,
            "assistant": "main",
        },
    )

    assert resp.status_code == 200
    assert resp.headers.get("content-type", "").startswith("application/json")
    assert resp.json()["answer"] == "OK"
    assert resp.json()["session_id"] == "ses_test"
    assert resp.json()["message_id"] == "msg_test"


def test_stream_true_emits_keepalive_before_answer(monkeypatch):
    app = create_app()

    import app.api.v2.chat as chat_module
    import app.utils.streaming as streaming_module

    async def delayed_stream(**kwargs):  # noqa: ANN003
        await asyncio.sleep(0.03)

        async def response_gen():
            yield {"type": "metadata", "session_id": "ses_test", "message_id": "msg_test"}
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
            "session_id": "ses_test",
            "query": "hello",
            "stream": True,
            "assistant": "main",
        },
    ) as resp:
        assert resp.status_code == 200
        assert resp.headers.get("content-type", "").startswith("text/event-stream")

        saw_keepalive = False
        saw_metadata = False
        saw_chunk = False

        for line in resp.iter_lines():
            if not line or not line.startswith("data: "):
                continue

            payload = json.loads(line.removeprefix("data: "))
            if payload == {"type": "progress", "message": "still working"}:
                saw_keepalive = True
            if payload == {
                "type": "metadata",
                "session_id": "ses_test",
                "message_id": "msg_test",
            }:
                saw_metadata = True
            if payload == {"type": "chunk", "chunk": "OK"}:
                saw_chunk = True
                break

        assert saw_keepalive is True
        assert saw_metadata is True
        assert saw_chunk is True


def test_chat_requires_session_id(monkeypatch):
    app = create_app()

    import app.api.v2.chat as chat_module

    monkeypatch.setattr(chat_module, "chat_service", _FakeChatService())

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

    assert resp.status_code == 422


def test_agent_stream_emits_metadata(monkeypatch):
    app = create_app()

    import app.api.v2.chat as chat_module

    class _FakeFlow:
        def __init__(self):
            self.state = type(
                "State",
                (),
                {
                    "generation_meta": {"token_usage": {"input_token": 1}},
                    "selected_assistant": "main",
                    "web_search_output": None,
                    "attachments": [],
                    "answer": "Agent answer",
                },
            )()

        async def kickoff_async(self, initial_state):  # noqa: ANN001
            self.state.answer = "Agent answer"
            return initial_state

    fake = _FakeChatService()
    monkeypatch.setattr(chat_module, "chat_service", fake)
    monkeypatch.setattr(chat_module, "get_agentic_rag_flow_streaming", lambda callback: _FakeFlow())

    client = TestClient(app)
    headers = {settings.API_KEY_NAME.lower(): settings.API_KEY}

    with client.stream(
        "POST",
        f"{settings.API_PREFIX}/chat/agent/stream",
        headers=headers,
        json={
            "user_id": "u1",
            "session_id": "ses_test",
            "query": "hello",
        },
    ) as resp:
        assert resp.status_code == 200
        assert resp.headers.get("content-type", "").startswith("text/event-stream")

        saw_metadata = False
        for line in resp.iter_lines():
            if not line or not line.startswith("data: "):
                continue

            payload = json.loads(line.removeprefix("data: "))
            if payload == {
                "type": "metadata",
                "session_id": "ses_test",
                "message_id": "msg_test",
            }:
                saw_metadata = True
            if payload == {"type": "end"}:
                break

        assert saw_metadata is True


def test_agent_stream_requires_session_id(monkeypatch):
    app = create_app()

    import app.api.v2.chat as chat_module

    monkeypatch.setattr(chat_module, "chat_service", _FakeChatService())

    client = TestClient(app)
    headers = {settings.API_KEY_NAME.lower(): settings.API_KEY}

    resp = client.post(
        f"{settings.API_PREFIX}/chat/agent/stream",
        headers=headers,
        json={
            "user_id": "u1",
            "query": "hello",
        },
    )

    assert resp.status_code == 422
