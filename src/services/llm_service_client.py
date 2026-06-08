from __future__ import annotations

import json
from collections.abc import AsyncGenerator
from typing import Any, BinaryIO

import httpx

from core.config import settings
from core.logger import logger


class LlmServiceClient:
    """HTTP client for the internal rest-api-llm service."""

    def __init__(self) -> None:
        base_url = (settings.LLM_SERVICE_URL or "").strip().rstrip("/")
        if not base_url:
            raise RuntimeError("LLM_SERVICE_URL is not configured")
        self.base_url = base_url
        self.timeout = httpx.Timeout(settings.LLM_SERVICE_TIMEOUT_SECONDS)
        self.header_name = settings.LLM_SERVICE_INTERNAL_HEADER
        self.internal_token = (settings.LLM_SERVICE_INTERNAL_TOKEN or "").strip()

    def _headers(self, *, json_content: bool = True) -> dict[str, str]:
        headers: dict[str, str] = {}
        if json_content:
            headers["Content-Type"] = "application/json"
        if self.internal_token:
            headers[self.header_name] = self.internal_token
        return headers

    def _url(self, path: str) -> str:
        return f"{self.base_url}{path}"

    def _raise_for_status(
        self, response: httpx.Response, *, path: str, body_text: str
    ) -> None:
        """Raise on HTTP errors, surfacing the upstream response body.

        httpx's ``raise_for_status`` omits the response body from the error
        message, so an upstream 422 from rest-api-llm (e.g. a request that
        fails ``LlmInferenceRequest`` validation) would otherwise be opaque.
        We log and attach the body so the real cause is visible.
        """
        if response.is_success:
            return
        detail = body_text.strip()
        logger.error(
            f"[LlmServiceClient] {response.request.method} {path} "
            f"-> {response.status_code}: {detail or '(empty body)'}"
        )
        raise httpx.HTTPStatusError(
            f"{response.status_code} from rest-api-llm {path}: "
            f"{detail or '(empty body)'}",
            request=response.request,
            response=response,
        )

    async def post_json(self, path: str, payload: dict[str, Any]) -> dict[str, Any]:
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            response = await client.post(
                self._url(path),
                json=payload,
                headers=self._headers(),
            )
            self._raise_for_status(response, path=path, body_text=response.text)
            data = response.json()
        if isinstance(data, dict) and "data" in data:
            data = data["data"]
        return data if isinstance(data, dict) else {}

    async def request_json(
        self,
        method: str,
        path: str,
        *,
        payload: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            response = await client.request(
                method,
                self._url(path),
                json=payload,
                headers=self._headers(json_content=payload is not None),
            )
            self._raise_for_status(response, path=path, body_text=response.text)
            if response.status_code == 204:
                return {}
            data = response.json()
        if isinstance(data, dict) and "data" in data:
            data = data["data"]
        return data if isinstance(data, dict) else {}

    async def stream_json(
        self, path: str, payload: dict[str, Any]
    ) -> AsyncGenerator[Any, None]:
        async with httpx.AsyncClient(timeout=None) as client:
            async with client.stream(
                "POST",
                self._url(path),
                json=payload,
                headers=self._headers(),
            ) as response:
                if not response.is_success:
                    # Streaming responses are lazy: read the body so the
                    # upstream error detail (e.g. a 422 validation error) is
                    # available before we raise.
                    body_bytes = await response.aread()
                    self._raise_for_status(
                        response,
                        path=path,
                        body_text=body_bytes.decode(errors="replace"),
                    )
                async for line in response.aiter_lines():
                    line = line.strip()
                    if not line or not line.startswith("data: "):
                        continue
                    raw = line.removeprefix("data: ").strip()
                    try:
                        yield json.loads(raw)
                    except json.JSONDecodeError:
                        yield raw

    async def transcribe_audio(
        self,
        *,
        file: BinaryIO,
        filename: str,
        content_type: str,
        language: str,
    ) -> dict[str, Any]:
        files = {"file": (filename, file, content_type)}
        data = {"language": language}
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            response = await client.post(
                self._url("/api/v1/speech-to-text/transcribe"),
                files=files,
                data=data,
                headers=self._headers(json_conftent=False),
            )
            response.raise_for_status()
            body = response.json()
        if isinstance(body, dict) and "data" in body:
            body = body["data"]
        return body if isinstance(body, dict) else {}

    async def ocr_file(
        self,
        *,
        file: BinaryIO,
        filename: str,
        content_type: str,
    ) -> dict[str, Any]:
        files = {"file": (filename, file, content_type)}
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            response = await client.post(
                self._url("/api/v1/ocr"),
                files=files,
                headers=self._headers(json_content=False),
            )
            response.raise_for_status()
            body = response.json()
        if isinstance(body, dict) and "data" in body:
            body = body["data"]
        return body if isinstance(body, dict) else {}

    async def ask_chat(self, payload: dict[str, Any]) -> dict[str, Any]:
        return await self.post_json("/api/v1/chat/ask", payload)

    async def embed_file(self, payload: dict[str, Any]) -> dict[str, Any]:
        return await self.post_json("/api/v1/embed", payload)

    async def search_project(self, payload: dict[str, Any]) -> dict[str, Any]:
        return await self.post_json("/api/v1/project/search", payload)

    async def search_files(self, payload: dict[str, Any]) -> dict[str, Any]:
        return await self.post_json("/api/v1/files/search", payload)

    async def delete_vectors(self, payload: dict[str, Any]) -> dict[str, Any]:
        return await self.post_json("/api/v1/vectors/delete", payload)

    async def memory_get_all(self, user_id: str) -> dict[str, Any]:
        return await self.request_json("GET", f"/api/v1/memory/user/{user_id}/")


_llm_service_client: LlmServiceClient | None = None


def get_llm_service_client() -> LlmServiceClient:
    global _llm_service_client
    if _llm_service_client is None:
        _llm_service_client = LlmServiceClient()
    return _llm_service_client


__all__ = ["LlmServiceClient", "get_llm_service_client"]
