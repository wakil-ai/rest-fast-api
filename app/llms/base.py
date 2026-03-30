from abc import ABC, abstractmethod
from collections.abc import AsyncGenerator

import tiktoken

from app.core.config import settings


class LLM(ABC):
    """Abstract base class for different model handlers."""

    _tokenizer = None
    _tokenizer_model: str | None = None

    def _get_tokenizer(self):
        model = getattr(self, "model", None) or settings.DEFAULT_CHAT_MODEL

        if self._tokenizer is not None and self._tokenizer_model == model:
            return self._tokenizer

        try:
            enc = tiktoken.encoding_for_model(model)
        except Exception:
            # Fallback encoding when model isn't known to tiktoken.
            enc = tiktoken.get_encoding("o200k_base")

        self._tokenizer = enc
        self._tokenizer_model = model
        return enc

    async def count_tokens(self, text: str) -> int:
        """Best-effort local token counting (no API calls)."""
        if not text:
            return 0
        enc = self._get_tokenizer()
        return len(enc.encode(text))

    @abstractmethod
    async def generate_response(
        self, user_prompt: str, system_prompt: str, stream: bool = settings.STREAM
    ) -> str | AsyncGenerator[str, None]:
        """
        Generate a response using the specific model.

        Returns:
            - str: Complete response if streaming is disabled
            - AsyncGenerator[str, None]: Streaming response if streaming is enabled
        """
        pass
