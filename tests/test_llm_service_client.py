import json
from collections.abc import AsyncIterator
from typing import Any, ClassVar

import pytest
from typing_extensions import Self

from core.config import settings
from services.llm_service_client import LlmServiceClient

Call = tuple[str, str, dict[str, Any]]


class FakeResponse:
    """`is_success` and `text` are what _raise_for_status reads on every path;
    `raise_for_status` is still used directly by memory_delete and file upload."""

    def __init__(
        self, payload: dict[str, Any] | None = None, status_code: int = 200
    ) -> None:
        self._payload: dict[str, Any] = payload or {}
        self.status_code = status_code

    @property
    def is_success(self) -> bool:
        return self.status_code < 400

    @property
    def text(self) -> str:
        return json.dumps(self._payload)

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")

    def json(self) -> dict[str, Any]:
        return self._payload


class FakeAsyncClient:
    calls: ClassVar[list[Call]] = []

    def __init__(self, *_: Any, **kwargs: Any) -> None:
        self.kwargs = kwargs

    async def __aenter__(self) -> Self:
        return self

    async def __aexit__(self, *_: object) -> None:
        return None

    async def post(self, url: str, **kwargs: Any) -> FakeResponse:
        self.calls.append(("POST", url, kwargs))
        return FakeResponse({"answer": "ok", "metadata": {"workflow": "test"}})

    async def request(self, method: str, url: str, **kwargs: Any) -> FakeResponse:
        self.calls.append((method, url, kwargs))
        return FakeResponse({"memories": [], "categories": [], "memory_ids": []})

    def stream(self, method: str, url: str, **kwargs: Any) -> "FakeStreamResponse":
        self.calls.append((method, url, kwargs))
        return FakeStreamResponse()


class FakeStreamResponse:
    status_code = 200

    async def __aenter__(self) -> Self:
        return self

    async def __aexit__(self, *_: object) -> None:
        return None

    @property
    def is_success(self) -> bool:
        return self.status_code < 400

    async def aiter_lines(self) -> AsyncIterator[str]:
        yield ""
        yield "data: " + json.dumps({"type": "chunk", "chunk": "hello"})
        yield "data: " + json.dumps({"type": "end"})


@pytest.fixture(autouse=True)
def configure_client(monkeypatch: pytest.MonkeyPatch) -> None:
    FakeAsyncClient.calls = []
    monkeypatch.setattr(settings, "LLM_SERVICE_URL", "http://llm.internal")
    monkeypatch.setattr(settings, "LLM_SERVICE_INTERNAL_TOKEN", "secret")
    monkeypatch.setattr(settings, "LLM_SERVICE_INTERNAL_HEADER", "x-internal-token")
    monkeypatch.setattr("services.llm_service_client.httpx.AsyncClient", FakeAsyncClient)


@pytest.mark.asyncio
async def test_post_json_sends_internal_token() -> None:
    client = LlmServiceClient()

    response = await client.ask_chat({"query": "hello"})

    assert response["answer"] == "ok"
    method, url, kwargs = FakeAsyncClient.calls[0]
    assert method == "POST"
    assert url == "http://llm.internal/api/v1/chat/ask"
    assert kwargs["headers"]["x-internal-token"] == "secret"


@pytest.mark.asyncio
async def test_stream_json_parses_sse_events() -> None:
    client = LlmServiceClient()

    events = [
        event async for event in client.stream_json("/api/v1/chat/ask/stream", {})
    ]

    assert events == [{"type": "chunk", "chunk": "hello"}, {"type": "end"}]


@pytest.mark.asyncio
async def test_memory_get_uses_get_method() -> None:
    client = LlmServiceClient()

    await client.memory_get_all("user_1")

    method, url, kwargs = FakeAsyncClient.calls[0]
    assert method == "GET"
    assert url == "http://llm.internal/api/v1/memory/user/user_1/"
    assert kwargs["headers"]["x-internal-token"] == "secret"


@pytest.mark.asyncio
async def test_search_files_uses_internal_endpoint() -> None:
    client = LlmServiceClient()

    await client.search_files({"file_ids": ["file_1"], "user_id": "user_1", "query": "q"})

    method, url, kwargs = FakeAsyncClient.calls[0]
    assert method == "POST"
    assert url == "http://llm.internal/api/v1/files/search"
    assert kwargs["json"]["file_ids"] == ["file_1"]


def _capture_timeout(monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    """Swap httpx.AsyncClient for a double that records the timeout it was built
    with, then refuses to stream. Both tests assert on construction, so reaching
    `stream` at all means the client was built and the assertion is already made.
    """
    captured: dict[str, Any] = {}

    class FakeClient:
        def __init__(self, timeout: Any = None, **_: Any) -> None:
            captured["timeout"] = timeout

        async def __aenter__(self) -> Self:
            return self

        async def __aexit__(self, *_: object) -> bool:
            return False

        def stream(self, *_: Any, **__: Any) -> Any:
            raise AssertionError("not reached in this test")

    monkeypatch.setattr("services.llm_service_client.httpx.AsyncClient", FakeClient)
    return captured


def _bare_client() -> LlmServiceClient:
    """__new__, not __init__: the constructor reads settings and builds a real
    httpx client, neither of which this assertion needs."""
    client = LlmServiceClient.__new__(LlmServiceClient)
    client.base_url = "http://llm"
    client.internal_token = ""
    client.header_name = "X-Internal"
    return client


async def test_stream_json_defaults_to_no_timeout(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Ordinary chat must keep its unlimited stream."""
    captured = _capture_timeout(monkeypatch)

    with pytest.raises(AssertionError):
        await _bare_client().stream_json("/x", {}, timeout=None).__anext__()

    assert captured["timeout"] is None


async def test_stream_json_forwards_an_explicit_timeout(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Delegation passes a real bound so an abandoned slot can be reclaimed."""
    captured = _capture_timeout(monkeypatch)

    with pytest.raises(AssertionError):
        await _bare_client().stream_json("/x", {}, timeout=900.0).__anext__()

    assert captured["timeout"] == 900.0
