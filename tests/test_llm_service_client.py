import json

import pytest

from core.config import settings
from services.llm_service_client import LlmServiceClient


class FakeResponse:
    def __init__(self, payload=None, status_code=200):
        self._payload = payload or {}
        self.status_code = status_code

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")

    def json(self):
        return self._payload


class FakeAsyncClient:
    calls = []

    def __init__(self, *args, **kwargs):
        self.kwargs = kwargs

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return None

    async def post(self, url, **kwargs):
        self.calls.append(("POST", url, kwargs))
        return FakeResponse({"answer": "ok", "metadata": {"workflow": "test"}})

    async def request(self, method, url, **kwargs):
        self.calls.append((method, url, kwargs))
        return FakeResponse({"memories": [], "categories": [], "memory_ids": []})

    def stream(self, method, url, **kwargs):
        self.calls.append((method, url, kwargs))
        return FakeStreamResponse()


class FakeStreamResponse:
    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return None

    def raise_for_status(self):
        return None

    async def aiter_lines(self):
        yield ""
        yield "data: " + json.dumps({"type": "chunk", "chunk": "hello"})
        yield "data: " + json.dumps({"type": "end"})


@pytest.fixture(autouse=True)
def configure_client(monkeypatch):
    FakeAsyncClient.calls = []
    monkeypatch.setattr(settings, "LLM_SERVICE_URL", "http://llm.internal")
    monkeypatch.setattr(settings, "LLM_SERVICE_INTERNAL_TOKEN", "secret")
    monkeypatch.setattr(settings, "LLM_SERVICE_INTERNAL_HEADER", "x-internal-token")
    monkeypatch.setattr("services.llm_service_client.httpx.AsyncClient", FakeAsyncClient)


@pytest.mark.asyncio
async def test_post_json_sends_internal_token():
    client = LlmServiceClient()

    response = await client.ask_chat({"query": "hello"})

    assert response["answer"] == "ok"
    method, url, kwargs = FakeAsyncClient.calls[0]
    assert method == "POST"
    assert url == "http://llm.internal/api/v1/chat/ask"
    assert kwargs["headers"]["x-internal-token"] == "secret"


@pytest.mark.asyncio
async def test_stream_json_parses_sse_events():
    client = LlmServiceClient()

    events = [
        event async for event in client.stream_json("/api/v1/chat/ask/stream", {})
    ]

    assert events == [{"type": "chunk", "chunk": "hello"}, {"type": "end"}]


@pytest.mark.asyncio
async def test_memory_get_uses_get_method():
    client = LlmServiceClient()

    await client.memory_get_all("user_1")

    method, url, kwargs = FakeAsyncClient.calls[0]
    assert method == "GET"
    assert url == "http://llm.internal/api/v1/memory/user/user_1/"
    assert kwargs["headers"]["x-internal-token"] == "secret"


@pytest.mark.asyncio
async def test_search_files_uses_internal_endpoint():
    client = LlmServiceClient()

    await client.search_files({"file_ids": ["file_1"], "user_id": "user_1", "query": "q"})

    method, url, kwargs = FakeAsyncClient.calls[0]
    assert method == "POST"
    assert url == "http://llm.internal/api/v1/files/search"
    assert kwargs["json"]["file_ids"] == ["file_1"]
