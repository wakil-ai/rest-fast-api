from unittest.mock import AsyncMock

from fastapi.testclient import TestClient

from app.core.config import settings
from app.main import create_app


class _FakeChatService:
    def validate_query_length(self, query: str) -> None:  # noqa: ARG002
        return None

    def extract_assistant_config(self, assistant_name: str):  # noqa: ARG002
        return 1, "collection"

    async def verify_user_credits(self, **kwargs):  # noqa: ANN003
        return None

    async def ask_question(self, **kwargs):  # noqa: ANN003
        return "OK"

    def create_streaming_response(self, generator):  # noqa: ANN001,ARG002
        raise AssertionError("create_streaming_response must not be called")


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
