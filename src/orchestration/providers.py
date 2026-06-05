"""LangChain-backed LLM handlers."""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import AsyncGenerator
from typing import Any

import tiktoken
from langchain_anthropic import ChatAnthropic
from langchain_core.messages import HumanMessage, SystemMessage
from langchain_google_genai import ChatGoogleGenerativeAI
from langchain_openai import ChatOpenAI

from src.core.config import settings
from src.core.langfuse_tracing import LlmRunName, langchain_invoke_config, traced_ainvoke
from src.core.logger import logger
from src.orchestration.gemini_prompt_cache import get_gemini_prompt_cache
from src.orchestration.text import message_content_to_plain_str

GEMINI_MODEL_ALIASES: dict[str, str] = {
    "gemini-3.1-pro": "gemini-3.1-pro-preview",
    "gemini-3-pro": "gemini-3-pro-preview",
    "gemini-3.1-flash": "gemini-3.1-flash-lite",
    "gemini-3-flash": "gemini-3-flash-preview",
}


def resolve_gemini_model_name(model_name: str | None) -> str:
    """Map short Gemini aliases to model ids served by the Google API."""
    if not isinstance(model_name, str) or not model_name.strip():
        return "gemini-3.1-pro-preview"
    name = model_name.strip()
    if name.startswith("models/"):
        name = name[len("models/") :]
    return GEMINI_MODEL_ALIASES.get(name, name)


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
            enc = tiktoken.get_encoding("o200k_base")

        self._tokenizer = enc
        self._tokenizer_model = model
        return enc

    async def count_tokens(self, text: str) -> int:
        if not text:
            return 0
        enc = self._get_tokenizer()
        return len(enc.encode(text))

    @abstractmethod
    async def generate_response(
        self, user_prompt: str, system_prompt: str, stream: bool = settings.STREAM
    ) -> str | AsyncGenerator[str, None]:
        pass


class LangChainChatModel(LLM):
    """Shared invoke/stream behavior for LangChain chat wrappers."""

    async def generate_response(
        self,
        user_prompt: str,
        system_prompt: str,
        stream: bool = settings.STREAM,
        *,
        run_name: str = LlmRunName.ASSISTANT_GENERATION,
        user_id: str | None = None,
        session_id: str | None = None,
    ) -> str | AsyncGenerator[str, None]:
        lc, effective_system_prompt = await self._prepare_invocation_model(
            system_prompt,
        )
        messages = self._messages(user_prompt, effective_system_prompt)
        try:
            if stream:
                return self._stream_messages(
                    lc,
                    messages,
                    run_name=run_name,
                    user_id=user_id,
                    session_id=session_id,
                )
            out = await traced_ainvoke(
                lc,
                messages,
                run_name=run_name,
                user_id=user_id,
                session_id=session_id,
                tags=[self.__class__.__name__],
            )
            return message_content_to_plain_str(getattr(out, "content", out))
        except Exception as error:
            logger.error(
                f"[{self.__class__.__name__}/LangChain] Generation error: {error}"
            )
            raise

    async def _prepare_invocation_model(self, system_prompt: str) -> tuple[Any, str]:
        return self._lc, system_prompt

    def _messages(self, user_prompt: str, system_prompt: str) -> list:
        messages = []
        if system_prompt:
            messages.append(SystemMessage(content=system_prompt))
        messages.append(HumanMessage(content=user_prompt))
        return messages

    async def _stream_messages(
        self,
        lc: Any,
        messages: list,
        *,
        run_name: str = LlmRunName.ASSISTANT_GENERATION,
        user_id: str | None = None,
        session_id: str | None = None,
    ) -> AsyncGenerator[str, None]:
        config = langchain_invoke_config(
            run_name,
            user_id=user_id,
            session_id=session_id,
            tags=[self.__class__.__name__],
        )
        async for chunk in self._streaming_model(lc).astream(messages, config or None):
            raw = getattr(chunk, "content", None)
            if not raw:
                continue
            piece = message_content_to_plain_str(raw)
            if piece:
                yield piece

    def _streaming_model(self, lc: Any | None = None):
        return lc or self._lc


class ChatGPT(LangChainChatModel):
    """OpenAI chat models via LangChain ``ChatOpenAI``."""

    def __init__(self, model_name: str | None = None):
        self.model = (model_name or settings.DEFAULT_CHAT_MODEL or "").strip()
        token_param = (
            "max_completion_tokens"
            if "gpt-5.2" in self.model or self.model.startswith("o1")
            else "max_tokens"
        )
        self._lc = ChatOpenAI(
            model=self.model,
            api_key=settings.OPENAI_API_KEY,
            temperature=settings.TEMPERATURE,
            **{token_param: settings.OUTPUT_MAX_TOKENS},
        )


class Novita(LangChainChatModel):
    """Novita OpenAI-compatible API via LangChain ``ChatOpenAI``."""

    MODEL_MAPPING = {
        "gpt-oss-120b": "openai/gpt-oss-120b",
        "gemma-3-27b": "google/gemma-3-27b-it",
    }

    def __init__(self, model_name: str | None = None):
        self.model = (
            self.MODEL_MAPPING.get(model_name or "", model_name)
            or settings.NOVITA_MODEL
        )
        self._lc = ChatOpenAI(
            model=self.model,
            api_key=settings.NOVITA_API_KEY or "",
            base_url=settings.NOVITA_API_BASE,
            temperature=settings.TEMPERATURE,
            max_tokens=settings.OUTPUT_MAX_TOKENS,
        )


class Claude(LangChainChatModel):
    """Anthropic Claude via ``langchain_anthropic.ChatAnthropic``."""

    def __init__(self, model_name: str = "claude-opus-4-5-20251101"):
        self.model = model_name
        self._lc = ChatAnthropic(
            model=self.model,
            anthropic_api_key=settings.ANTHROPIC_API_KEY,
            temperature=settings.TEMPERATURE,
            max_tokens=settings.OUTPUT_MAX_TOKENS,
        )


def _gemini_supports_thinking_level(model: str) -> bool:
    m = (model or "").lower()
    if "flash-lite" in m or "gemma" in m or "embedding" in m:
        return False
    return True


class Gemini(LangChainChatModel):
    """Google Gemini via LangChain ``ChatGoogleGenerativeAI``."""

    def __init__(self, model_name: str | None = None):
        self.model = (
            resolve_gemini_model_name(
                model_name or settings.DEFAULT_CHAT_MODEL or "gemini-2.5-flash"
            )
        ).strip()
        self._kwargs: dict[str, Any] = {
            "model": self.model,
            "google_api_key": settings.GEMINI_API_KEY,
            "temperature": settings.TEMPERATURE,
            "max_output_tokens": settings.OUTPUT_MAX_TOKENS,
        }
        if _gemini_supports_thinking_level(self.model):
            level = settings.GEMINI_LANGCHAIN_THINKING_LEVEL or "low"
            level = str(level).strip().lower()
            if level in ("off", "false", "0", "none"):
                level = "minimal"
            allowed = frozenset({"minimal", "low", "medium", "high"})
            if level not in allowed:
                level = "low"
            self._kwargs["thinking_level"] = level
            self._kwargs["include_thoughts"] = True
        self._lc = ChatGoogleGenerativeAI(**self._kwargs)

    async def _prepare_invocation_model(self, system_prompt: str) -> tuple[Any, str]:
        cached_content = await get_gemini_prompt_cache().get_or_create_cached_content_name(
            model=self.model,
            system_prompt=system_prompt,
        )
        if not cached_content:
            return self._lc, system_prompt
        return (
            ChatGoogleGenerativeAI(
                **self._kwargs,
                cached_content=cached_content,
            ),
            "",
        )

    def _streaming_model(self, lc: Any | None = None):
        return (lc or self._lc).bind(streaming=True)


__all__ = [
    "LLM",
    "LangChainChatModel",
    "resolve_gemini_model_name",
    "message_content_to_plain_str",
    "ChatGPT",
    "Claude",
    "Gemini",
    "Novita",
]
