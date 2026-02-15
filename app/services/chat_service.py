import asyncio
from collections.abc import AsyncGenerator
from typing import Any

from fastapi.responses import StreamingResponse

from app.chains.chat_chain import ChatChain
from app.core.assistants import AssistantConfig
from app.core.config import settings
from app.core.exceptions import (
    ChatGenerationException,
    InsufficientCreditsException,
    QueryTooLongException,
)
from app.core.logger import logger
from app.models.chat import AgenticRAGRequest, ChatResponse, MessagePair
from app.orchestration.flow import AgenticRAGFlow
from app.services.rate_limit_service import RateLimitAssistantType, RateLimitService
from app.utils.streaming import format_streaming_response, get_streaming_headers


class ChatService:
    """Service class to handle user questions and generate answers."""

    def __init__(self):
        self.chat_chain = ChatChain()
        self.rate_limit_service = RateLimitService()

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
        assistant_type: RateLimitAssistantType,
        required_credits: int = 1,
    ) -> None:
        """Verify user has sufficient credits and deduct them."""
        try:
            is_allowed, credits_remaining, limit = await self.rate_limit_service.check_and_decrement_credits(
                user_id=user_id,
                assistant_type=assistant_type,
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
    def extract_debug_context(flow_service: AgenticRAGFlow) -> str:
        """Extract debug context from flow state in development mode."""
        if not settings.DEVELOPMENT_MODE:
            return ""

        try:
            return (
                flow_service.state.retrieval_docs
                + "Relevance Score: "
                + str(flow_service.state.web_search_output)
            )
        except Exception as e:
            logger.warning(f"Could not extract debug context: {e}")
            return "No retrieved contents available."

    @staticmethod
    def create_response(answer: str, debug_context: str = "") -> ChatResponse:
        """Create ChatResponse with optional debug information."""
        if settings.DEVELOPMENT_MODE and debug_context:
            return ChatResponse(answer=answer, retrieved_contents=debug_context)
        return ChatResponse(answer=answer)

    @staticmethod
    def create_streaming_response(generator) -> StreamingResponse:
        """Create streaming response with appropriate headers."""
        return StreamingResponse(
            format_streaming_response(generator),
            media_type="text/event-stream",
            headers=get_streaming_headers(),
        )

    async def ask_question(
        self,
        user_id: str,
        query: str,
        chat_history: list[MessagePair] | None = None,
        stream: bool = settings.STREAM,
        file_ids: list[str] | None = None,
        assistant: str = "main",
        model_name: str | None = None,
    ) -> str | AsyncGenerator[str, None] | tuple[str, dict[str, Any]]:
        """
        Handle the question by retrieving context and generating an answer.

        Args:
            user_id: User identifier
            query: User's question
            chat_history: Previous conversation history
            stream: Enable streaming response
            file_ids: List of file IDs to use as context
            assistant: Assistant name
            model_name: Optional model name for generation

        Returns:
            - str: Complete answer if streaming disabled and dev mode disabled
            - Tuple[str, Dict]: Answer + debug data if streaming disabled and dev mode enabled
            - AsyncGenerator[str, None]: Streaming answer if streaming enabled

        Raises:
            ChatGenerationException: If answer generation fails
        """
        try:
            answer = await self.chat_chain.generate_answer(
                user_id=user_id,
                query=query,
                chat_history=chat_history,
                stream=stream,
                file_ids=file_ids,
                assistant=assistant,
                model_name=model_name,
            )
            return answer
        except Exception as e:
            logger.error(f"Error generating answer: {e}", exc_info=True)
            raise ChatGenerationException(f"Failed to generate answer: {str(e)}")
