"""Best-effort explicit context caching for Gemini system prompts."""

from __future__ import annotations

import asyncio
import hashlib
import time
from dataclasses import dataclass
from functools import lru_cache

from src.core.config import settings
from src.core.logger import logger
from src.utils.tokens import count_tokens


@dataclass(slots=True)
class _CacheEntry:
    name: str
    expires_at: float


class GeminiPromptCache:
    """Create and reuse Gemini cached contents for large repeated system prompts."""

    def __init__(self) -> None:
        self._entries: dict[str, _CacheEntry] = {}
        self._failed_until: dict[str, float] = {}
        self._lock = asyncio.Lock()

    async def get_or_create_cached_content_name(
        self,
        *,
        model: str,
        system_prompt: str,
    ) -> str | None:
        if not self._enabled():
            return None
        prompt = (system_prompt or "").strip()
        if not prompt or not settings.GEMINI_API_KEY:
            return None
        if count_tokens(prompt) < settings.GEMINI_EXPLICIT_CACHE_MIN_TOKENS:
            return None

        key = self._cache_key(model, prompt)
        now = time.time()
        entry = self._entries.get(key)
        if entry and entry.expires_at > now:
            return entry.name
        if self._failed_until.get(key, 0) > now:
            return None

        async with self._lock:
            now = time.time()
            entry = self._entries.get(key)
            if entry and entry.expires_at > now:
                return entry.name
            if self._failed_until.get(key, 0) > now:
                return None

            try:
                name = await asyncio.to_thread(
                    self._create_cached_content,
                    model,
                    prompt,
                    key,
                )
            except Exception as exc:
                self._failed_until[key] = now + 300
                logger.warning(
                    "Gemini explicit prompt cache creation failed; using uncached "
                    "request. model=%s error=%s",
                    model,
                    exc,
                    exc_info=True,
                )
                return None

            self._prune(now)
            self._entries[key] = _CacheEntry(
                name=name,
                expires_at=now + self._ttl_seconds(),
            )
            logger.info("Gemini explicit prompt cache created: %s", name)
            return name

    def _create_cached_content(self, model: str, system_prompt: str, key: str) -> str:
        from google import genai
        from google.genai import types

        client = genai.Client(api_key=settings.GEMINI_API_KEY)
        cache = client.caches.create(
            model=self._sdk_model_name(model),
            config=types.CreateCachedContentConfig(
                displayName=f"wakilai-{key[:24]}",
                systemInstruction=system_prompt,
                ttl=settings.GEMINI_EXPLICIT_CACHE_TTL,
            ),
        )
        name = getattr(cache, "name", None)
        if not name:
            raise RuntimeError("Gemini cache create response did not include a name")
        return str(name)

    @staticmethod
    def _enabled() -> bool:
        return bool(settings.GEMINI_EXPLICIT_CACHE_ENABLED)

    @staticmethod
    def _cache_key(model: str, system_prompt: str) -> str:
        payload = f"{model}\0{system_prompt}".encode("utf-8")
        return hashlib.sha256(payload).hexdigest()

    @staticmethod
    def _sdk_model_name(model: str) -> str:
        cleaned = (model or "").strip()
        if cleaned.startswith("models/"):
            return cleaned
        return f"models/{cleaned}"

    @staticmethod
    def _ttl_seconds() -> int:
        raw = str(settings.GEMINI_EXPLICIT_CACHE_TTL or "3600s").strip().lower()
        try:
            if raw.endswith("s"):
                return max(1, int(float(raw[:-1])))
            if raw.endswith("m"):
                return max(1, int(float(raw[:-1]) * 60))
            if raw.endswith("h"):
                return max(1, int(float(raw[:-1]) * 3600))
            return max(1, int(float(raw)))
        except ValueError:
            return 3600

    def _prune(self, now: float) -> None:
        self._entries = {
            key: entry
            for key, entry in self._entries.items()
            if entry.expires_at > now
        }
        max_entries = max(1, int(settings.GEMINI_EXPLICIT_CACHE_MAX_LOCAL_ENTRIES))
        if len(self._entries) <= max_entries:
            return
        for key, _entry in sorted(
            self._entries.items(),
            key=lambda item: item[1].expires_at,
        )[: len(self._entries) - max_entries]:
            self._entries.pop(key, None)


@lru_cache
def get_gemini_prompt_cache() -> GeminiPromptCache:
    return GeminiPromptCache()
