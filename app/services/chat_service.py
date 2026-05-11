import asyncio
from collections.abc import AsyncGenerator
from time import perf_counter
from typing import Any, cast

from fastapi import HTTPException, Request
from fastapi.responses import StreamingResponse

from app.agents.registry import invoke_chat_agent_run
from app.agents.streaming import astream_chat_with_persistence
from app.core.assistants import AssistantConfig
from app.core.config import settings
from app.core.dependencies import (
    get_agentic_rag_flow_streaming,
    get_chat_orchestrator,
    get_chat_history_service,
    get_project_service,
    get_rate_limit_service,
)
from app.core.exceptions import (
    ChatException,
    ChatGenerationException,
    FlowExecutionException,
    InsufficientCreditsException,
    InvalidInputError,
    QueryTooLongException,
)
from app.core.logger import logger
from app.models.chat import (
    AgenticRAGRequest,
    AssistantType,
    ChatRequest,
    ChatResponse,
    ModelInfoResponse,
)
from app.utils.streaming import format_streaming_response, get_streaming_headers


class ChatService:
    """Service class to handle user questions and generate answers."""

    def __init__(self):
        self.chat_orchestrator = get_chat_orchestrator()
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
    def build_agentic_state(
        request: AgenticRAGRequest, message_id: str
    ) -> dict[str, Any]:
        """Build initial state for agentic RAG flow."""
        return {
            "query": request.query,
            "user_id": request.user_id,
            "session_id": request.session_id,
            "message_id": message_id,
            "project_id": request.project_id,
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

    async def prepare_chat_request(
        self,
        user_id: str,
        session_id: str,
        project_id: str | None = None,
    ) -> tuple[str, str]:
        """Validate chat session, optionally link it to a project, and allocate message id."""
        session = await self.chat_history_service.ensure_session_for_user(
            user_id=user_id,
            session_id=session_id,
        )
        resolved_session_id = session.get("session_id") or session.get("_id")
        if not resolved_session_id:
            raise ChatGenerationException("Failed to resolve chat session.")

        if project_id:
            await get_project_service().get_project(project_id, user_id)
            existing = session.get("project_id")
            if existing and existing != project_id:
                raise InvalidInputError(
                    "This session is already linked to a different project."
                )
            if not existing:
                await self.chat_history_service.attach_session_to_project(
                    resolved_session_id, project_id
                )

        return resolved_session_id, self.chat_history_service.create_message_id()

    def build_message_metadata(
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

        if selected_assistant := generation_meta.get("selected_assistant"):
            metadata["selected_assistant"] = selected_assistant

        if workflow := generation_meta.get("workflow"):
            metadata["workflow"] = workflow

        if used_web_search := generation_meta.get("used_web_search"):
            metadata["used_web_search"] = used_web_search

        return metadata

    def schedule_message_persistence(
        self,
        *,
        user_id: str,
        session_id: str,
        message_id: str,
        query: str,
        answer: str,
        file_ids: list[str] | None,
        metadata: dict[str, Any],
        project_id: str | None = None,
    ) -> None:
        async def persist() -> None:
            try:
                meta = dict(metadata)
                if project_id:
                    meta["project_id"] = project_id
                await self.chat_history_service.upsert_message(
                    session_id=session_id,
                    message_id=message_id,
                    user_id=user_id,
                    file_ids=file_ids,
                    content={"query": query, "response": answer},
                    metadata=meta,
                )
                if project_id:
                    await get_project_service().increment_stat(project_id, "chats", 1)
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
        project_id: str | None = None,
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
            metadata = self.build_message_metadata(
                assistant=assistant,
                stream=True,
                latency_ms=latency_ms,
                generation_meta=generation_meta,
            )
            self.schedule_message_persistence(
                user_id=user_id,
                session_id=session_id,
                message_id=message_id,
                query=query,
                answer="".join(answer_chunks),
                file_ids=file_ids,
                metadata=metadata,
                project_id=project_id,
            )

        return wrapped()

    async def ask_question(
        self,
        user_id: str,
        session_id: str,
        message_id: str,
        query: str,
        stream: bool = settings.STREAM,
        file_ids: list[str] | None = None,
        assistant: str = "main",
        project_id: str | None = None,
    ) -> str | AsyncGenerator[Any, None] | tuple[str, dict[str, Any]]:
        """
        Handle the question by retrieving context and generating an answer.

        Args:
            user_id: User identifier
            session_id: Session identifier used to load previous persisted messages
            query: User's question
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
            answer = await self.chat_orchestrator.generate_answer(
                user_id=user_id,
                session_id=session_id,
                query=query,
                stream=stream,
                file_ids=file_ids,
                assistant=assistant,
                project_id=project_id,
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
                        project_id=project_id,
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
                        project_id=project_id,
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
                    project_id=project_id,
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

            metadata = self.build_message_metadata(
                assistant=assistant,
                stream=False,
                latency_ms=latency_ms,
                generation_meta=generation_meta,
            )
            self.schedule_message_persistence(
                user_id=user_id,
                session_id=session_id,
                message_id=message_id,
                query=query,
                answer=answer_text,
                file_ids=file_ids,
                metadata=metadata,
                project_id=project_id,
            )

            response_meta = dict(generation_meta)
            response_meta["latency_ms"] = latency_ms
            return answer_text, response_meta
        except InvalidInputError:
            raise
        except Exception as e:
            logger.error(f"Error generating answer: {e}", exc_info=True)
            raise ChatGenerationException(f"Failed to generate answer: {str(e)}")

    @staticmethod
    def is_dt_team_request(raw_request: Request) -> bool:
        return bool(raw_request.headers.get(settings.DT_API_KEY_NAME.lower()))

    @staticmethod
    def append_dt_team_disclaimer(answer: str, *, is_dt_team_request: bool) -> str:
        if not is_dt_team_request:
            return answer
        return f"{answer}{settings.DT_TEAM_DISCLAIMER}"

    async def handle_chat_ask(
        self, request: ChatRequest, raw_request: Request
    ) -> ChatResponse | StreamingResponse:
        """Full ``POST /chat/ask`` pipeline (all assistants)."""
        try:
            self.validate_query_length(request.query)

            assistant_name = AssistantConfig.validate_assistant_or_default(
                request.assistant.value if request.assistant else None
            )

            logger.info(f"Using assistant: {assistant_name}")

            credit_cost, _ = self.extract_assistant_config(assistant_name)

            await self.verify_user_credits(
                user_id=request.user_id,
                assistant_type=assistant_name,
                required_credits=credit_cost,
            )

            should_stream = settings.STREAM if request.stream is None else request.stream
            is_dt = self.is_dt_team_request(raw_request)

            session_id, message_id = await self.prepare_chat_request(
                user_id=request.user_id,
                session_id=request.session_id,
                project_id=request.project_id,
            )

            dt_suffix = settings.DT_TEAM_DISCLAIMER if is_dt else ""

            if should_stream:
                started_stream = perf_counter()
                return self.create_streaming_response(
                    astream_chat_with_persistence(
                        user_id=request.user_id,
                        session_id=session_id,
                        message_id=message_id,
                        query=request.query,
                        file_ids=request.file_ids,
                        assistant=assistant_name,
                        started_at=started_stream,
                        dt_team_disclaimer_suffix=dt_suffix,
                        project_id=request.project_id,
                    )
                )

            started_at = perf_counter()
            try:
                answer, lc_meta, generation_ctx = await invoke_chat_agent_run(
                    user_id=request.user_id,
                    session_id=session_id,
                    message_id=message_id,
                    query=request.query,
                    file_ids=request.file_ids,
                    assistant=assistant_name,
                    project_id=request.project_id,
                )
            except RuntimeError as e:
                logger.error(
                    f"[ChatService] LangChain / LangGraph assistant misconfiguration: {e}",
                    exc_info=True,
                )
                raise ChatGenerationException(str(e)) from e

            answer_out = self.append_dt_team_disclaimer(
                answer, is_dt_team_request=is_dt
            )
            latency_ms = int((perf_counter() - started_at) * 1000)
            merged_meta = dict(lc_meta or {})
            merged_meta["latency_ms"] = latency_ms
            attachments_out = merged_meta.get("attachments")
            metadata = self.build_message_metadata(
                assistant=generation_ctx.assistant_name,
                stream=False,
                latency_ms=latency_ms,
                generation_meta=merged_meta,
            )
            self.schedule_message_persistence(
                user_id=request.user_id,
                session_id=session_id,
                message_id=message_id,
                query=request.query,
                answer=answer_out,
                file_ids=request.file_ids,
                metadata=metadata,
                project_id=request.project_id,
            )
            return ChatResponse(
                answer=answer_out,
                session_id=session_id,
                message_id=message_id,
                latency_ms=latency_ms,
                attachments=attachments_out if attachments_out else None,
            )

        except ChatException:
            raise
        except HTTPException:
            raise
        except Exception as e:
            logger.error(
                f"[ChatService] Unexpected error in handle_chat_ask: {str(e)}",
                exc_info=True,
            )
            raise ChatGenerationException()

    async def handle_agentic_rag_stream(
        self, request: AgenticRAGRequest
    ) -> StreamingResponse:
        """Build streaming response for agentic RAG (``POST /chat/agent/stream``)."""
        try:
            self.validate_query_length(request.query)

            await self.verify_user_credits(
                user_id=request.user_id,
                assistant_type=AssistantType.MAIN,
                required_credits=settings.CREDIT_COST_MAIN_ASSISTANT,
            )

            session_id, message_id = await self.prepare_chat_request(
                user_id=request.user_id,
                session_id=request.session_id,
                project_id=request.project_id,
            )

            progress_queue: asyncio.Queue = asyncio.Queue()

            async def progress_callback(event: dict) -> None:
                await progress_queue.put(event)

            flow = get_agentic_rag_flow_streaming(progress_callback)

            initial_state = self.build_agentic_state(request, message_id=message_id)
            started_at = perf_counter()

            async def response_generator() -> AsyncGenerator[Any, None]:
                yield {
                    "type": "metadata",
                    "session_id": session_id,
                    "message_id": message_id,
                }

                flow_task = asyncio.create_task(flow.kickoff_async(initial_state))

                flow_complete = False
                while not (flow_complete and progress_queue.empty()):
                    if flow_task.done() and not flow_complete:
                        flow_complete = True
                        try:
                            await flow_task
                        except Exception as e:
                            logger.exception(
                                "[ChatService] Agent stream pipeline failed: %s", e
                            )
                            yield {
                                "type": "error",
                                "message": f"An error occurred: {str(e)}",
                            }
                            break

                        try:
                            latency_ms = int((perf_counter() - started_at) * 1000)
                            generation_meta = dict(flow.state.generation_meta or {})
                            generation_meta["workflow"] = "two_stage_pipeline"
                            if flow.state.selected_assistant:
                                generation_meta["selected_assistant"] = (
                                    flow.state.selected_assistant
                                )
                            wo = flow.state.web_search_output
                            if wo:
                                docs = (
                                    wo.get("docs")
                                    if isinstance(wo, dict)
                                    else getattr(wo, "docs", None)
                                )
                                generation_meta["used_web_search"] = bool(docs)
                            if flow.state.attachments:
                                generation_meta["attachments"] = flow.state.attachments

                            metadata = self.build_message_metadata(
                                assistant="main",
                                stream=True,
                                latency_ms=latency_ms,
                                generation_meta=generation_meta,
                            )
                            self.schedule_message_persistence(
                                user_id=request.user_id,
                                session_id=session_id,
                                message_id=message_id,
                                query=request.query,
                                answer=flow.state.answer or "",
                                file_ids=request.file_ids,
                                metadata=metadata,
                                project_id=request.project_id,
                            )
                        except Exception as e:
                            logger.exception(
                                "[ChatService] Agent stream post-flow "
                                "(metadata/persistence): %s",
                                e,
                            )
                            yield {
                                "type": "error",
                                "message": f"An error occurred: {str(e)}",
                            }
                            break

                    try:
                        event = await asyncio.wait_for(
                            progress_queue.get(), timeout=0.1
                        )
                        yield event
                    except asyncio.TimeoutError:
                        continue
                    except Exception as e:
                        logger.error(f"Error getting progress event: {e}")
                        break

            return self.create_streaming_response(response_generator())

        except ChatException:
            raise
        except Exception as e:
            logger.error(
                f"[ChatService] Unexpected error in handle_agentic_rag_stream: {str(e)}",
                exc_info=True,
            )
            raise FlowExecutionException("Failed to stream agentic RAG response.")

    @staticmethod
    def get_public_assistants_payload() -> dict[str, Any]:
        assistants = AssistantConfig.get_public_assistants()
        return {
            "assistants": [
                {
                    "name": name,
                    "description": config["description"],
                    "credit_cost": config["credit_cost"],
                }
                for name, config in assistants.items()
            ]
        }

    @staticmethod
    def get_model_info_response() -> ModelInfoResponse:
        return ModelInfoResponse(
            service_provider=settings.LLM_PROVIDER,
            embedding_model=settings.EMBEDDING_MODEL,
            stream=settings.STREAM,
            top_k=settings.TOP_K,
            alpha=settings.ALPHA,
            temperature=settings.TEMPERATURE,
        )
