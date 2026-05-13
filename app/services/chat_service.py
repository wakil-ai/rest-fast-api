import asyncio
from collections.abc import AsyncGenerator
from time import perf_counter
from typing import Any, cast

from fastapi import HTTPException, Request
from fastapi.responses import StreamingResponse

from app.core.assistants import AssistantConfig
from app.core.config import settings
from app.core.dependencies import (
    get_chat_history_service,
    get_orchestration_service,
    get_project_service,
    get_rate_limit_service,
)
from app.core.exceptions import (
    ChatException,
    ChatGenerationException,
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
from app.utils.streaming import (
    format_progress_event,
    format_streaming_response,
    get_streaming_headers,
)


def _orchestration_exception_detail(exc: BaseException) -> str:
    """Stable message for logs and HTTP detail; some failures use empty str(exc)."""
    text = str(exc).strip()
    if text:
        return text
    cause = exc.__cause__
    if cause is not None and str(cause).strip():
        return (
            f"{type(exc).__name__} ({type(cause).__name__}: {str(cause).strip()})"
        )
    ctx = exc.__context__
    if (
        ctx is not None
        and str(ctx).strip()
        and ctx is not cause
    ):
        return f"{type(exc).__name__} ({type(ctx).__name__}: {str(ctx).strip()})"

    name = type(exc).__name__
    if isinstance(exc, NotImplementedError):
        return (
            f"{name} (empty message). See server traceback — e.g. LangGraph async "
            "checkpoint methods on a sync-only saver, or an unimplemented "
            "checkpoint/store base method."
        )

    return (
        f"{name} with no message; see server logs (traceback). "
        "If this is a missing-service error, verify GEMINI_API_KEY and Redis/Mongo URIs."
    )


class ChatService:
    """Service class to handle user questions and generate answers."""

    def __init__(self):
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
    ) -> tuple[str, str, str | None]:
        """Validate chat session, optionally link it to a project, and allocate message id."""
        session = await self.chat_history_service.ensure_session_for_user(
            user_id=user_id,
            session_id=session_id,
        )
        resolved_session_id = session.get("session_id") or session.get("_id")
        if not resolved_session_id:
            raise ChatGenerationException("Failed to resolve chat session.")

        resolved_project_id = project_id or session.get("project_id")
        if resolved_project_id:
            await get_project_service().get_project(resolved_project_id, user_id)
            existing = session.get("project_id")
            if existing and existing != resolved_project_id:
                raise InvalidInputError(
                    "This session is already linked to a different project."
                )
            if not existing:
                await self.chat_history_service.attach_session_to_project(
                    resolved_session_id, resolved_project_id
                )

        return (
            resolved_session_id,
            self.chat_history_service.create_message_id(),
            str(resolved_project_id) if resolved_project_id else None,
        )

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

    def orchestration_generation_meta(
        self,
        *,
        assistant: str,
        orchestration_result: dict[str, Any],
    ) -> dict[str, Any]:
        state = orchestration_result.get("result") or {}
        meta: dict[str, Any] = {
            "workflow": "orchestration_graph",
            "selected_assistant": state.get("selected_assistant") or assistant,
        }
        if state.get("web_search_output"):
            meta["used_web_search"] = True
        if attachments := state.get("attachments"):
            meta["attachments"] = attachments
        return meta

    async def run_orchestrated_chat(
        self,
        *,
        user_id: str,
        session_id: str,
        message_id: str,
        query: str,
        assistant: str,
        file_ids: list[str] | None = None,
        project_id: str | None = None,
        file_context: str | None = None,
    ) -> tuple[str, dict[str, Any]]:
        service = get_orchestration_service()
        payload = service.build_payload(
            query=query,
            user_id=user_id,
            session_id=session_id,
            message_id=message_id,
            assistant_name=assistant,
            file_ids=file_ids,
            project_id=project_id,
            file_context=file_context,
        )
        result = await service.arun(payload)
        return (
            str(result.get("final_answer") or ""),
            self.orchestration_generation_meta(
                assistant=assistant,
                orchestration_result=result,
            ),
        )

    async def astream_orchestrated_chat(
        self,
        *,
        user_id: str,
        session_id: str,
        message_id: str,
        query: str,
        file_ids: list[str] | None,
        assistant: str,
        started_at: float,
        is_dt_team_request: bool = False,
        project_id: str | None = None,
        file_context: str | None = None,
    ) -> AsyncGenerator[Any, None]:
        yield {
            "type": "metadata",
            "session_id": session_id,
            "message_id": message_id,
        }
        yield await format_progress_event(
            "orchestration",
            "in_progress",
            "Retrieving context and generating an answer…",
        )

        try:
            service = get_orchestration_service()
            payload = service.build_payload(
                query=query,
                user_id=user_id,
                session_id=session_id,
                message_id=message_id,
                assistant_name=assistant,
                file_ids=file_ids,
                project_id=project_id,
                file_context=file_context,
            )
            last_state = await service.prepare_final_state(payload)
            prev_attachment_urls: frozenset[str] = frozenset()
            raw_atts = last_state.get("attachments")
            if isinstance(raw_atts, list) and raw_atts:
                urls = frozenset(
                    str((a or {}).get("url") or "").strip()
                    for a in raw_atts
                    if isinstance(a, dict) and str((a or {}).get("url") or "").strip()
                )
                if urls and urls != prev_attachment_urls:
                    prev_attachment_urls = urls
                    yield {"type": "attachments", "attachments": list(raw_atts)}

            answer_chunks: list[str] = []
            async for item in service.astream_final_answer(last_state):
                if isinstance(item, str):
                    answer_chunks.append(item)
                yield item

            answer = "".join(answer_chunks).strip()
            last_state["final_answer"] = answer
            last_state["retrieval_context"] = ""
            result_wrapped = {
                "original_query": query,
                "rewritten_query": last_state.get("rewritten_query"),
                "retrieval_context": last_state.get("retrieval_context", ""),
                "final_answer": answer,
                "result": last_state,
            }
            generation_meta = self.orchestration_generation_meta(
                assistant=assistant,
                orchestration_result=result_wrapped,
            )
        except Exception as error:
            detail = _orchestration_exception_detail(error)
            logger.error(
                f"[ChatService] Orchestration failed: {detail}",
                exc_info=True,
            )
            raise ChatGenerationException(detail) from error

        answer_out = self.append_dt_team_disclaimer(
            answer,
            is_dt_team_request=is_dt_team_request,
        )
        if is_dt_team_request and settings.DT_TEAM_DISCLAIMER:
            yield settings.DT_TEAM_DISCLAIMER

        latency_ms = int((perf_counter() - started_at) * 1000)
        merged_meta = dict(generation_meta)
        merged_meta["latency_ms"] = latency_ms
        metadata = self.build_message_metadata(
            assistant=str(merged_meta.get("selected_assistant") or assistant),
            stream=True,
            latency_ms=latency_ms,
            generation_meta=merged_meta,
        )
        await self._persist_assistant_message_safe(
            user_id=user_id,
            session_id=session_id,
            message_id=message_id,
            query=query,
            answer=answer_out,
            file_ids=file_ids,
            metadata=metadata,
            project_id=project_id,
        )

    async def _persist_assistant_message_safe(
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
        asyncio.create_task(
            self._persist_assistant_message_safe(
                user_id=user_id,
                session_id=session_id,
                message_id=message_id,
                query=query,
                answer=answer,
                file_ids=file_ids,
                metadata=metadata,
                project_id=project_id,
            )
        )

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

            should_stream = (
                settings.STREAM if request.stream is None else request.stream
            )
            is_dt = self.is_dt_team_request(raw_request)

            session_id, message_id, resolved_project_id = (
                await self.prepare_chat_request(
                    user_id=request.user_id,
                    session_id=request.session_id,
                    project_id=request.project_id,
                )
            )

            if should_stream:
                started_stream = perf_counter()
                return self.create_streaming_response(
                    self.astream_orchestrated_chat(
                        user_id=request.user_id,
                        session_id=session_id,
                        message_id=message_id,
                        query=request.query,
                        file_ids=request.file_ids,
                        assistant=assistant_name,
                        started_at=started_stream,
                        is_dt_team_request=is_dt,
                        project_id=resolved_project_id,
                        file_context=request.file_context,
                    )
                )

            started_at = perf_counter()
            try:
                answer, generation_meta = await self.run_orchestrated_chat(
                    user_id=request.user_id,
                    session_id=session_id,
                    message_id=message_id,
                    query=request.query,
                    assistant=assistant_name,
                    file_ids=request.file_ids,
                    project_id=resolved_project_id,
                    file_context=request.file_context,
                )
            except Exception as error:
                detail = _orchestration_exception_detail(error)
                logger.error(
                    f"[ChatService] Orchestration failed: {detail}",
                    exc_info=True,
                )
                raise ChatGenerationException(detail) from error

            answer_out = self.append_dt_team_disclaimer(
                answer, is_dt_team_request=is_dt
            )
            latency_ms = int((perf_counter() - started_at) * 1000)
            merged_meta = dict(generation_meta)
            merged_meta["latency_ms"] = latency_ms
            attachments_out = merged_meta.get("attachments")
            metadata = self.build_message_metadata(
                assistant=str(merged_meta.get("selected_assistant") or assistant_name),
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
                project_id=resolved_project_id,
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
        """Build streaming response for deep-research chat (``POST /chat/agent/stream``)."""
        try:
            self.validate_query_length(request.query)

            assistant_name = "deepresearch"
            credit_cost, _ = self.extract_assistant_config(assistant_name)

            await self.verify_user_credits(
                user_id=request.user_id,
                assistant_type=assistant_name,
                required_credits=credit_cost,
            )

            session_id, message_id, resolved_project_id = (
                await self.prepare_chat_request(
                    user_id=request.user_id,
                    session_id=request.session_id,
                    project_id=request.project_id,
                )
            )

            started_at = perf_counter()
            return self.create_streaming_response(
                self.astream_orchestrated_chat(
                    user_id=request.user_id,
                    session_id=session_id,
                    message_id=message_id,
                    query=request.query,
                    file_ids=request.file_ids,
                    assistant=assistant_name,
                    started_at=started_at,
                    project_id=resolved_project_id,
                    file_context=request.file_context,
                )
            )

        except ChatException:
            raise
        except Exception as e:
            logger.error(
                f"[ChatService] Unexpected error in handle_agentic_rag_stream: {str(e)}",
                exc_info=True,
            )
            raise ChatGenerationException("Failed to stream deep-research response.")

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
