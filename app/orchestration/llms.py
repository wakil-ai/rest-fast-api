"""LangGraph-backed final LLM generation with Redis ``thread_id`` memory (no tools)."""

from __future__ import annotations

import asyncio
import warnings
from collections.abc import AsyncGenerator, Iterator
from typing import Any

from langchain.agents import create_agent
from langchain_core.messages import (
    AIMessage,
    AIMessageChunk,
    BaseMessage,
    HumanMessage,
    SystemMessage,
)
from langchain_core.language_models.chat_models import BaseChatModel
from langchain_google_genai import ChatGoogleGenerativeAI

from app.core.assistants import AssistantConfig
from app.core.config import settings
from app.core.langfuse_tracing import (
    LlmRunName,
    atraced_llm_span,
    langchain_invoke_config,
    merge_langchain_config,
    record_final_answer_span_input,
    traced_ainvoke,
)
from app.orchestration.providers import LLM, resolve_gemini_model_name
from app.orchestration.text import message_content_to_plain_str


async def ainvoke_lite_classification_chat(
    llm: BaseChatModel,
    *,
    system_prompt: str,
    user_prompt: str,
    run_name: str = LlmRunName.INTENT_RECOGNITION,
    user_id: str | None = None,
    session_id: str | None = None,
) -> str:
    """Single-turn system + user call; returns assistant text only."""
    messages = [
        SystemMessage(content=system_prompt),
        HumanMessage(content=user_prompt),
    ]
    out = await traced_ainvoke(
        llm,
        messages,
        run_name=run_name,
        user_id=user_id,
        session_id=session_id,
    )
    return message_content_to_plain_str(getattr(out, "content", out))


class LangChain(LLM):
    """Final reasoning model: prompt + retrieval context, history on ``thread_id``."""

    def __init__(
        self,
        model_name: str | None = None,
        *,
        checkpointer: Any | None = None,
    ):
        self.checkpointer = checkpointer
        self.model = self.resolve_model_name(model_name)

    @classmethod
    def resolve_model_name(cls, model_name: str | None = None) -> str:
        if isinstance(model_name, str) and model_name.strip():
            return resolve_gemini_model_name(model_name)
        raw = getattr(settings, "GEMINI_LANGCHAIN_CHAT_MODEL", "")
        if isinstance(raw, str) and raw.strip():
            return resolve_gemini_model_name(raw)
        default = settings.DEFAULT_CHAT_MODEL or ""
        if isinstance(default, str) and default.strip().startswith("gemini"):
            return resolve_gemini_model_name(default)
        return resolve_gemini_model_name("gemini-2.5-flash")

    def build_chat_model(self) -> ChatGoogleGenerativeAI:
        if not settings.GEMINI_API_KEY:
            raise RuntimeError(
                "GEMINI_API_KEY is required for LangChain / LangGraph generation."
            )

        level = settings.GEMINI_LANGCHAIN_THINKING_LEVEL or "low"
        level = str(level).strip().lower()
        if level in ("off", "false", "0", "none"):
            level = "minimal"
        allowed = frozenset({"minimal", "low", "medium", "high"})
        if level not in allowed:
            level = "low"

        return ChatGoogleGenerativeAI(
            model=self.model,
            google_api_key=settings.GEMINI_API_KEY,
            temperature=settings.TEMPERATURE,
            max_output_tokens=settings.OUTPUT_MAX_TOKENS,
            thinking_level=level,
            include_thoughts=True,
            streaming=True,
        )

    def compile_chat(self, *, system_prompt: str) -> Any:
        if self.checkpointer is None:
            raise RuntimeError(
                "LangChain.compile_chat requires a Redis checkpointer. "
                "Pass checkpointer= to LangChain(...)."
            )
        return create_agent(
            self.build_chat_model(),
            [],
            system_prompt=system_prompt,
            checkpointer=self.checkpointer,
        )

    @staticmethod
    def thread_config(thread_id: str) -> dict[str, Any]:
        return {"configurable": {"thread_id": thread_id}}

    async def invoke_turn(
        self,
        *,
        thread_id: str,
        query: str,
        system_prompt: str,
        assistant_name: str = "main",
        user_id_for_logs: str = "",
        session_id: str | None = None,
    ) -> tuple[str, dict[str, Any]]:
        canonical = AssistantConfig.validate_assistant_or_default(assistant_name)
        agent = self.compile_chat(system_prompt=system_prompt)

        invoke_config = merge_langchain_config(
            self.thread_config(thread_id),
            langchain_invoke_config(
                LlmRunName.FINAL_ANSWER,
                user_id=user_id_for_logs or None,
                session_id=session_id,
                tags=[f"assistant:{canonical}"],
            ),
        )
        user_text = query.strip()
        async with atraced_llm_span(
            LlmRunName.FINAL_ANSWER,
            user_id=user_id_for_logs or None,
            session_id=session_id,
        ) as span:
            record_final_answer_span_input(
                span,
                system_prompt=system_prompt,
                user_prompt=user_text,
            )
            outcome = await agent.ainvoke(
                {"messages": [HumanMessage(content=user_text)]},
                invoke_config,
            )
        msgs: list[BaseMessage] = list(outcome.get("messages") or [])
        answer = self._last_ai_text(msgs)

        meta: dict[str, Any] = {}
        usage = getattr(msgs[-1], "usage_metadata", None) if msgs else None
        if isinstance(usage, dict):
            meta["token_usage"] = usage

        meta["model"] = self.model
        meta["workflow"] = f"langgraph_final_gemini_{canonical}"
        meta["langgraph_assistant"] = canonical
        meta["langgraph_thread_id"] = thread_id
        if user_id_for_logs:
            meta["lc_user_ref"] = user_id_for_logs
        return answer or "(empty model response)", meta

    async def astream_turn(
        self,
        *,
        thread_id: str,
        query: str,
        system_prompt: str,
        assistant_name: str = "main",
        user_id_for_logs: str = "",
        session_id: str | None = None,
    ) -> AsyncGenerator[str | dict[str, Any], None]:
        canonical = AssistantConfig.validate_assistant_or_default(assistant_name)
        agent = self.compile_chat(system_prompt=system_prompt)

        stream_config = merge_langchain_config(
            self.thread_config(thread_id),
            langchain_invoke_config(
                LlmRunName.FINAL_ANSWER,
                user_id=user_id_for_logs or None,
                session_id=session_id,
                tags=[f"assistant:{canonical}"],
            ),
        )

        collected_meta: dict[str, Any] = {
            "model": self.model,
            "workflow": f"langgraph_final_gemini_{canonical}_stream",
            "langgraph_assistant": canonical,
            "langgraph_thread_id": thread_id,
        }

        user_text = query.strip()
        input_state = {"messages": [HumanMessage(content=user_text)]}
        emitted_text = False
        final_ai_text = ""
        streamed_answer_prefix = ""
        streamed_think_prefix = ""
        sse_slice = self._sse_text_slice_limit()

        async with atraced_llm_span(
            LlmRunName.FINAL_ANSWER,
            user_id=user_id_for_logs or None,
            session_id=session_id,
        ) as span:
            record_final_answer_span_input(
                span,
                system_prompt=system_prompt,
                user_prompt=user_text,
            )
            with warnings.catch_warnings():
                warnings.filterwarnings(
                    "ignore",
                    message=r"Inheritance class AiohttpClientSession from ClientSession is discouraged",
                    category=DeprecationWarning,
                )
                stream = agent.astream(
                    input_state,
                    stream_config,
                    stream_mode="messages",
                    subgraphs=True,
                    version="v2",
                )
                async for event in stream:
                    chunk = self._stream_messages_event_to_message(event)
                    if chunk is None or not isinstance(chunk, BaseMessage):
                        continue

                    if isinstance(chunk, AIMessageChunk):
                        for kind, piece in self._stream_pieces(chunk):
                            if not piece:
                                continue
                            if kind == "think":
                                delta, streamed_think_prefix = self._delta_stream_text(
                                    previous=streamed_think_prefix,
                                    piece=piece,
                                )
                                if not delta:
                                    continue
                                yield {"type": "think", "chunk": delta}
                                await asyncio.sleep(0)
                                continue

                            delta, streamed_answer_prefix = self._delta_stream_text(
                                previous=streamed_answer_prefix,
                                piece=piece,
                            )
                            if not delta:
                                continue
                            emitted_text = True
                            for part in self._iter_text_slices_for_sse(
                                delta, max_chars=sse_slice
                            ):
                                yield part
                                await asyncio.sleep(0)

                        usage = getattr(chunk, "usage_metadata", None)
                        if isinstance(usage, dict):
                            collected_meta["token_usage"] = usage
                        continue

                    if isinstance(chunk, AIMessage):
                        usage = getattr(chunk, "usage_metadata", None)
                        if isinstance(usage, dict):
                            collected_meta["token_usage"] = usage
                        final_ai_text = self._answer_text(chunk)

        if not emitted_text and final_ai_text:
            for part in self._iter_text_slices_for_sse(final_ai_text, max_chars=sse_slice):
                yield part

        yield {"type": "_generation_meta", "meta": collected_meta}

    async def generate_response(
        self,
        user_prompt: str,
        system_prompt: str,
        stream: bool = settings.STREAM,
        *,
        thread_id: str,
        assistant_name: str = "main",
        user_id_for_logs: str = "",
        session_id: str | None = None,
    ) -> str | AsyncGenerator[str, None]:
        if stream:
            return self._generate_streaming(
                user_prompt=user_prompt,
                system_prompt=system_prompt,
                thread_id=thread_id,
                assistant_name=assistant_name,
                user_id_for_logs=user_id_for_logs,
                session_id=session_id,
            )
        answer, _meta = await self.invoke_turn(
            thread_id=thread_id,
            query=user_prompt,
            system_prompt=system_prompt,
            assistant_name=assistant_name,
            user_id_for_logs=user_id_for_logs,
            session_id=session_id,
        )
        return answer

    async def _generate_streaming(
        self,
        *,
        user_prompt: str,
        system_prompt: str,
        thread_id: str,
        assistant_name: str = "main",
        user_id_for_logs: str = "",
        session_id: str | None = None,
    ) -> AsyncGenerator[str | dict[str, Any], None]:
        async for item in self.astream_turn(
            thread_id=thread_id,
            query=user_prompt,
            system_prompt=system_prompt,
            assistant_name=assistant_name,
            user_id_for_logs=user_id_for_logs,
            session_id=session_id,
        ):
            if isinstance(item, (str, dict)):
                yield item

    @staticmethod
    def _message_text(msg: AIMessage | AIMessageChunk) -> str:
        content = getattr(msg, "content", None)
        if isinstance(content, str):
            return content
        if isinstance(content, list):
            parts: list[str] = []
            for block in content:
                if isinstance(block, str):
                    parts.append(block)
                elif isinstance(block, dict) and "text" in block:
                    parts.append(str(block["text"]))
            return "".join(parts)
        return ""

    @classmethod
    def _stream_messages_event_to_message(cls, event: Any) -> BaseMessage | None:
        if isinstance(event, dict) and event.get("type") == "messages":
            data = event.get("data")
            if isinstance(data, tuple) and data and isinstance(data[0], BaseMessage):
                return data[0]
            if isinstance(data, list) and data and isinstance(data[0], BaseMessage):
                return data[0]
            return None
        if isinstance(event, tuple) and event:
            if isinstance(event[0], BaseMessage):
                return event[0]
            if (
                len(event) == 3
                and event[1] == "messages"
                and isinstance(event[2], tuple)
                and event[2]
                and isinstance(event[2][0], BaseMessage)
            ):
                return event[2][0]
            if (
                len(event) == 2
                and isinstance(event[1], tuple)
                and event[1]
                and isinstance(event[1][0], BaseMessage)
            ):
                return event[1][0]
        if isinstance(event, BaseMessage):
            return event
        return None

    @staticmethod
    def _sse_text_slice_limit() -> int:
        try:
            n = int(settings.STREAM_SSE_MAX_RESPONSE_CHARS)
        except (TypeError, ValueError):
            n = 200
        return max(48, min(n, 4096))

    @staticmethod
    def _iter_text_slices_for_sse(text: str, *, max_chars: int) -> Iterator[str]:
        if not text:
            return
        if len(text) <= max_chars:
            yield text
            return
        i = 0
        n = len(text)
        min_break = max(16, max_chars // 3)
        while i < n:
            end = min(i + max_chars, n)
            if end < n:
                window = text[i:end]
                br = window.rfind("\n")
                if br >= min_break:
                    end = i + br + 1
                else:
                    sp = window.rfind(" ")
                    if sp >= min_break:
                        end = i + sp + 1
            yield text[i:end]
            i = end

    @classmethod
    def _visible_text_piece(cls, msg: AIMessage | AIMessageChunk) -> str:
        t = getattr(msg, "text", None)
        if t is not None:
            s = str(t)
            if s:
                return s
        return cls._message_text(msg)

    @staticmethod
    def _delta_stream_text(*, previous: str, piece: str) -> tuple[str, str]:
        if not piece:
            return "", previous
        if previous and piece.startswith(previous):
            return piece[len(previous) :], piece
        return piece, previous + piece

    @classmethod
    def _stream_pieces(cls, msg: AIMessage | AIMessageChunk) -> list[tuple[str, str]]:
        pieces: list[tuple[str, str]] = []
        try:
            blocks = msg.content_blocks
        except Exception:
            blocks = None
        if blocks:
            for block in blocks:
                if not isinstance(block, dict):
                    continue
                block_type = block.get("type")
                if block_type == "reasoning":
                    text = block.get("reasoning")
                    if text:
                        pieces.append(("think", str(text)))
                elif block_type == "text":
                    text = block.get("text")
                    if text:
                        pieces.append(("answer", str(text)))

        content = getattr(msg, "content", None)
        if isinstance(content, list):
            if not any(kind == "think" for kind, _ in pieces):
                for block in content:
                    if not isinstance(block, dict):
                        continue
                    if block.get("type") != "thinking":
                        continue
                    text = block.get("thinking")
                    if text:
                        pieces.insert(0, ("think", str(text)))
                        break

            if not pieces:
                for block in content:
                    if isinstance(block, str):
                        if block:
                            pieces.append(("answer", block))
                    elif isinstance(block, dict):
                        block_type = block.get("type")
                        if block_type == "thinking":
                            text = block.get("thinking")
                            if text:
                                pieces.append(("think", str(text)))
                        else:
                            text = block.get("text")
                            if text:
                                pieces.append(("answer", str(text)))
            return [(kind, text) for kind, text in pieces if text]

        if pieces:
            return [(kind, text) for kind, text in pieces if text]

        text = cls._visible_text_piece(msg)
        if text:
            pieces.append(("answer", text))
        return pieces

    @classmethod
    def _answer_text(cls, msg: AIMessage | AIMessageChunk) -> str:
        return "".join(
            text for kind, text in cls._stream_pieces(msg) if kind == "answer"
        ).strip()

    @classmethod
    def _last_ai_text(cls, messages: list[BaseMessage]) -> str:
        for msg in reversed(messages):
            if isinstance(msg, AIMessage):
                text = cls._answer_text(msg)
                if text:
                    return text
        return ""


__all__ = [
    "LangChain",
    "ainvoke_lite_classification_chat",
    "message_content_to_plain_str",
]
