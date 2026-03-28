import asyncio
from collections.abc import AsyncGenerator
from time import perf_counter
from typing import Any, cast

from fastapi.responses import StreamingResponse

from app.core.assistants import AssistantConfig
from app.core.config import settings
from app.core.dependencies import (
    get_chat_chain,
    get_chat_history_service,
    get_rate_limit_service,
)
from app.core.exceptions import (
    ChatGenerationException,
    InvalidInputError,
    InsufficientCreditsException,
    QueryTooLongException,
)
from app.core.logger import logger
from app.models.chat import AgenticRAGRequest, AssistantType, ChatResponse, MessagePair
from app.utils.streaming import format_streaming_response, get_streaming_headers


class ChatService:
    """Service class to handle user questions and generate answers."""

    def __init__(self):
        self.chat_chain = get_chat_chain()
        self.chat_history_service = get_chat_history_service()
        self.rate_limit_service = get_rate_limit_service()

    @staticmethod
    def validate_query_length(query: str) -> None:
        """Validate query does not exceed maximum length."""
        if len(query) > settings.MAX_QUERY_LENGTH:
            raise QueryTooLongException(
                query_length=len(query), max_length=settings.MAX_QUERY_LENGTH
            )

    async def verify_user_credits(
        self,
        user_id: str,
        assistant_type: AssistantType | str = "main",
        required_credits: int = 1,
    ) -> None:
        """Verify user has sufficient credits and deduct them."""
        try:
            is_allowed, credits_remaining, limit = (
                await self.rate_limit_service.check_and_decrement_credits(
                    user_id=user_id,
                    assistant_type=cast(AssistantType, assistant_type),
                )
            )

            if not is_allowed:
                raise InsufficientCreditsException(
                    credits_remaining=credits_remaining,
                    limit=limit,
                    required_credits=required_credits,
                )
        except InsufficientCreditsException:
            raise
        except Exception as e:
            logger.error(f"Error checking user credits: {e}")
            raise ChatGenerationException("Failed to verify user credits.")

    @staticmethod
    def extract_assistant_config(assistant_name: str) -> tuple[int, str]:
        """Extract credit cost and collection name from assistant config."""
        try:
            return (
                AssistantConfig.get_credit_cost(assistant_name),
                AssistantConfig.get_collection_name(assistant_name),
            )
        except Exception as e:
            logger.error(f"Error extracting assistant config: {e}")
            raise ChatGenerationException("Failed to load assistant configuration.")

    @staticmethod
    def build_agentic_state(request: AgenticRAGRequest) -> dict[str, Any]:
        """Build initial state for agentic RAG flow."""
        return {
            "query": request.query,
            "user_id": request.user_id,
            "session_id": request.session_id,
            "file_ids": request.file_ids,
        }

    @staticmethod
    def create_response(
        answer: str,
        session_id: str,
        message_id: str,
        latency_ms: int | None = None,
    ) -> ChatResponse:
        """Create ChatResponse."""
        return ChatResponse(
            answer=answer,
            session_id=session_id,
            message_id=message_id,
            latency_ms=latency_ms,
        )

    @staticmethod
    def create_streaming_response(
        generator: AsyncGenerator[Any, None],
    ) -> StreamingResponse:
        """Create streaming response with appropriate headers."""
        return StreamingResponse(
            format_streaming_response(generator),
            media_type="text/event-stream",
            headers=get_streaming_headers(),
        )

    async def prepare_chat_request(self, user_id: str, session_id: str) -> tuple[str, str]:
        """Validate chat session and allocate a message ID before generation."""
        session = await self.chat_history_service.ensure_session_for_user(
            user_id=user_id,
            session_id=session_id,
        )
        resolved_session_id = session.get("session_id") or session.get("_id")
        if not resolved_session_id:
            raise ChatGenerationException("Failed to resolve chat session.")

        return resolved_session_id, self.chat_history_service.create_message_id()

    def _build_message_metadata(
        self,
        *,
        assistant: str,
        stream: bool,
        latency_ms: int,
        generation_meta: dict[str, Any] | None,
    ) -> dict[str, Any]:
        metadata: dict[str, Any] = {
            "assistant": assistant,
            "stream": stream,
            "latency_ms": latency_ms,
        }

        generation_meta = generation_meta or {}

        if model := generation_meta.get("model"):
            metadata["model"] = model

        if attachments := generation_meta.get("attachments"):
            metadata["attachments"] = attachments

        if token_usage := generation_meta.get("token_usage"):
            metadata["token_usage"] = token_usage

        return metadata

    def _schedule_message_persistence(
        self,
        *,
        user_id: str,
        session_id: str,
        message_id: str,
        query: str,
        answer: str,
        file_ids: list[str] | None,
        metadata: dict[str, Any],
    ) -> None:
        async def persist() -> None:
            try:
                await self.chat_history_service.upsert_message(
                    session_id=session_id,
                    message_id=message_id,
                    user_id=user_id,
                    file_ids=file_ids,
                    content={"query": query, "response": answer},
                    metadata=metadata,
                )
            except Exception as e:
                logger.warning(
                    f"Failed to persist message {message_id} for session {session_id}: {e}",
                    exc_info=True,
                )

        asyncio.create_task(persist())

    async def _wrap_streaming_answer(
        self,
        *,
        response: AsyncGenerator[Any, None],
        user_id: str,
        session_id: str,
        message_id: str,
        query: str,
        file_ids: list[str] | None,
        assistant: str,
        started_at: float,
    ) -> AsyncGenerator[Any, None]:
        async def wrapped() -> AsyncGenerator[Any, None]:
            answer_chunks: list[str] = []
            generation_meta: dict[str, Any] = {}

            yield {
                "type": "metadata",
                "session_id": session_id,
                "message_id": message_id,
            }

            async for item in response:
                if isinstance(item, dict) and item.get("type") == "_generation_meta":
                    generation_meta = item.get("meta") or {}
                    continue

                if isinstance(item, str):
                    answer_chunks.append(item)

                yield item

            latency_ms = int((perf_counter() - started_at) * 1000)
            metadata = self._build_message_metadata(
                assistant=assistant,
                stream=True,
                latency_ms=latency_ms,
                generation_meta=generation_meta,
            )
            self._schedule_message_persistence(
                user_id=user_id,
                session_id=session_id,
                message_id=message_id,
                query=query,
                answer="".join(answer_chunks),
                file_ids=file_ids,
                metadata=metadata,
            )

        return wrapped()

    async def ask_question(
        self,
        user_id: str,
        session_id: str,
        message_id: str,
        query: str,
        chat_history: list[MessagePair] | None = None,
        stream: bool = settings.STREAM,
        file_ids: list[str] | None = None,
        assistant: str = "main",
    ) -> str | AsyncGenerator[Any, None] | tuple[str, dict[str, Any]]:
        """
        Handle the question by retrieving context and generating an answer.

        Args:
            user_id: User identifier
            query: User's question
            chat_history: Previous conversation history
            stream: Enable streaming response
            file_ids: List of file IDs to use as context
            assistant: Assistant name

        Returns:
            - str: Complete answer if streaming disabled and dev mode disabled
            - Tuple[str, Dict]: Answer + debug data if streaming disabled and dev mode enabled
            - AsyncGenerator[Any, None]: Streaming answer if streaming enabled

        Raises:
            ChatGenerationException: If answer generation fails
        """
        try:
            started_at = perf_counter()
            answer = await self.chat_chain.generate_answer(
                user_id=user_id,
                query=query,
                chat_history=chat_history,
                stream=stream,
                file_ids=file_ids,
                assistant=assistant,
            )

            if stream:
                if isinstance(answer, tuple):
                    answer_text, generation_meta = answer

                    async def tuple_stream() -> AsyncGenerator[Any, None]:
                        yield answer_text
                        yield {"type": "_generation_meta", "meta": generation_meta}

                    return await self._wrap_streaming_answer(
                        response=tuple_stream(),
                        user_id=user_id,
                        session_id=session_id,
                        message_id=message_id,
                        query=query,
                        file_ids=file_ids,
                        assistant=assistant,
                        started_at=started_at,
                    )

                if isinstance(answer, str):

                    async def string_stream() -> AsyncGenerator[Any, None]:
                        yield answer
                        yield {"type": "_generation_meta", "meta": {}}

                    return await self._wrap_streaming_answer(
                        response=string_stream(),
                        user_id=user_id,
                        session_id=session_id,
                        message_id=message_id,
                        query=query,
                        file_ids=file_ids,
                        assistant=assistant,
                        started_at=started_at,
                    )

                return await self._wrap_streaming_answer(
                    response=cast(AsyncGenerator[Any, None], answer),
                    user_id=user_id,
                    session_id=session_id,
                    message_id=message_id,
                    query=query,
                    file_ids=file_ids,
                    assistant=assistant,
                    started_at=started_at,
                )

            latency_ms = int((perf_counter() - started_at) * 1000)

            if isinstance(answer, tuple):
                answer_text, generation_meta = answer
            elif isinstance(answer, str):
                answer_text, generation_meta = answer, {}
            else:
                raise ChatGenerationException(
                    "Unexpected streaming response for non-streaming request."
                )

            metadata = self._build_message_metadata(
                assistant=assistant,
                stream=False,
                latency_ms=latency_ms,
                generation_meta=generation_meta,
            )
            self._schedule_message_persistence(
                user_id=user_id,
                session_id=session_id,
                message_id=message_id,
                query=query,
                answer=answer_text,
                file_ids=file_ids,
                metadata=metadata,
            )

            response_meta = dict(generation_meta)
            response_meta["latency_ms"] = latency_ms
            return answer_text, response_meta
        except InvalidInputError:
            raise
        except Exception as e:
            logger.error(f"Error generating answer: {e}", exc_info=True)
            raise ChatGenerationException(f"Failed to generate answer: {str(e)}")
