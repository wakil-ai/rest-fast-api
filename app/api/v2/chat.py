import asyncio
from collections.abc import AsyncGenerator
from time import perf_counter
from typing import Any, cast

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import StreamingResponse

from app.core.assistants import AssistantConfig
from app.core.config import settings
from app.core.dependencies import get_agentic_rag_flow_streaming, get_chat_service
from app.core.exceptions import (
    ChatException,
    ChatGenerationException,
    FlowExecutionException,
)
from app.core.logger import logger
from app.models.chat import (
    AgenticRAGRequest,
    ChatRequest,
    ChatResponse,
    ModelInfoResponse,
)
from app.utils.streaming import format_streaming_response, get_streaming_headers

router = APIRouter(prefix="/chat", tags=["Chat"])

chat_service = get_chat_service()

DT_TEAM_DISCLAIMER = (
    "\n\n> *DIQQAT! Ushbu protsessual hujjat loyihasi “WakilAI” sun’iy "
    "intellekt tizimi yordamida shakllantirildi. Hujjatni sudga taqdim "
    "etishdan avval malakali va professional advokat bilan maslahatlashish "
    "tavsiya etiladi.*"
)


def _is_dt_team_request(request: Request) -> bool:
    return bool(request.headers.get(settings.DT_API_KEY_NAME.lower()))


def _append_dt_team_disclaimer(answer: str, *, is_dt_team_request: bool) -> str:
    if not is_dt_team_request:
        return answer
    return f"{answer}{DT_TEAM_DISCLAIMER}"


async def _stream_chat_answer(
    request: ChatRequest,
    is_dt_team_request: bool,
    assistant_name: str,
    session_id: str,
    message_id: str,
) -> AsyncGenerator[Any, None]:
    response = await chat_service.ask_question(
        user_id=request.user_id,
        session_id=session_id,
        message_id=message_id,
        query=request.query,
        stream=True,
        file_ids=request.file_ids,
        assistant=assistant_name,
    )

    if isinstance(response, tuple):
        answer, meta = response
        yield answer
        if is_dt_team_request:
            yield DT_TEAM_DISCLAIMER

        attachments = meta.get("attachments") if isinstance(meta, dict) else None
        if attachments:
            yield {"type": "attachments", "attachments": attachments}
        return

    if isinstance(response, str):
        yield response
        if is_dt_team_request:
            yield DT_TEAM_DISCLAIMER
        return

    async for item in cast(AsyncGenerator[Any, None], response):
        yield item

    if is_dt_team_request:
        yield DT_TEAM_DISCLAIMER


@router.post("/ask", summary="Ask a legal question")
async def ask_question(request: ChatRequest, raw_request: Request):
    """
    Ask a question related to Uzbek legal documents.
    Retrieves context from vector DB and generates an answer.
    """
    try:
        chat_service.validate_query_length(request.query)

        assistant_name = AssistantConfig.validate_assistant_or_default(
            request.assistant.value if request.assistant else None
        )

        logger.info(f"Using assistant: {assistant_name}")

        credit_cost, _ = chat_service.extract_assistant_config(assistant_name)

        await chat_service.verify_user_credits(
            user_id=request.user_id,
            assistant_type=assistant_name,
            required_credits=credit_cost,
        )

        should_stream = settings.STREAM if request.stream is None else request.stream
        is_dt_team_request = _is_dt_team_request(raw_request)

        if should_stream:
            session_id, message_id = await chat_service.prepare_chat_request(
                user_id=request.user_id,
                session_id=request.session_id,
            )
            return chat_service.create_streaming_response(
                _stream_chat_answer(
                    request,
                    is_dt_team_request,
                    assistant_name,
                    session_id,
                    message_id,
                )
            )

        session_id, message_id = await chat_service.prepare_chat_request(
            user_id=request.user_id,
            session_id=request.session_id,
        )

        response = await chat_service.ask_question(
            user_id=request.user_id,
            session_id=session_id,
            message_id=message_id,
            query=request.query,
            stream=should_stream,
            file_ids=request.file_ids,
            assistant=assistant_name,
        )

        if isinstance(response, tuple):
            answer, meta = response
            return ChatResponse(
                answer=_append_dt_team_disclaimer(
                    answer, is_dt_team_request=is_dt_team_request
                ),
                session_id=session_id,
                message_id=message_id,
                latency_ms=meta.get("latency_ms"),
                attachments=meta.get("attachments") or None,
            )

        if not isinstance(response, str):
            raise ChatGenerationException(
                "Unexpected streaming response for non-streaming request."
            )

        return ChatResponse(
            answer=_append_dt_team_disclaimer(
                response, is_dt_team_request=is_dt_team_request
            ),
            session_id=session_id,
            message_id=message_id,
        )

    except ChatException:
        raise
    except HTTPException:
        raise
    except Exception as e:
        logger.error(
            f"[ChatAPI] Unexpected error in ask_question: {str(e)}", exc_info=True
        )
        raise ChatGenerationException()


@router.post("/agent/stream", summary="Stream legal question answer via agentic RAG")
async def stream_agentic_rag(request: AgenticRAGRequest):
    """
    Execute legal QA flow using agentic RAG with streaming response.

    Streams progress events for each pipeline step:
    - progress: Step-by-step updates
    - chunk: Final answer characters
    - end: Stream completion
    """
    try:
        chat_service.validate_query_length(request.query)

        await chat_service.verify_user_credits(
            user_id=request.user_id,
            assistant_type="deepresearch",
            required_credits=settings.CREDIT_COST_DEEPRESEARCH,
        )

        session_id, message_id = await chat_service.prepare_chat_request(
            user_id=request.user_id,
            session_id=request.session_id,
        )

        progress_queue = asyncio.Queue()

        async def progress_callback(event: dict):
            await progress_queue.put(event)

        flow = get_agentic_rag_flow_streaming(progress_callback)

        initial_state = chat_service.build_agentic_state(request, message_id=message_id)
        started_at = perf_counter()

        async def response_generator():
            yield {
                "type": "metadata",
                "session_id": session_id,
                "message_id": message_id,
            }

            flow_task = asyncio.create_task(flow.kickoff_async(initial_state))

            flow_complete = False
            while not (flow_complete and progress_queue.empty()):
                # Check if flow is done
                if flow_task.done() and not flow_complete:
                    flow_complete = True
                    # Await the result to catch any exceptions
                    try:
                        await flow_task
                        latency_ms = int((perf_counter() - started_at) * 1000)
                        generation_meta = dict(flow.state.generation_meta or {})
                        generation_meta["workflow"] = "agentic_rag"
                        if flow.state.selected_assistant:
                            generation_meta["selected_assistant"] = (
                                flow.state.selected_assistant
                            )
                        if flow.state.web_search_output:
                            generation_meta["used_web_search"] = bool(
                                flow.state.web_search_output.get("docs")
                            )
                        if flow.state.attachments:
                            generation_meta["attachments"] = flow.state.attachments

                        metadata = chat_service.build_message_metadata(
                            assistant="deepresearch",
                            stream=True,
                            latency_ms=latency_ms,
                            generation_meta=generation_meta,
                        )
                        chat_service.schedule_message_persistence(
                            user_id=request.user_id,
                            session_id=session_id,
                            message_id=message_id,
                            query=request.query,
                            answer=flow.state.answer or "",
                            file_ids=request.file_ids,
                            metadata=metadata,
                        )
                    except Exception as e:
                        logger.error("Flow execution error", exc_info=True)
                        yield {
                            "type": "error",
                            "message": f"An error occurred: {str(e)}",
                        }
                        break

                # Try to get events from queue
                try:
                    event = await asyncio.wait_for(progress_queue.get(), timeout=0.1)
                    yield event
                except asyncio.TimeoutError:
                    # No events available, continue loop
                    continue
                except Exception as e:
                    logger.error(f"Error getting progress event: {e}")
                    break

        return StreamingResponse(
            format_streaming_response(response_generator()),
            media_type="text/event-stream",
            headers=get_streaming_headers(),
        )

    except ChatException:
        raise
    except Exception as e:
        logger.error(
            f"[AgenticRAGStream] Unexpected error in stream_agentic_rag: {str(e)}",
            exc_info=True,
        )
        raise FlowExecutionException("Failed to stream agentic RAG response.")


@router.get("/assistants", summary="Get available assistants")
async def get_assistants():
    """Returns information about available assistants."""
    try:
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
    except Exception as e:
        logger.error(f"Error retrieving assistants: {e}")
        raise ChatGenerationException("Failed to retrieve assistants configuration.")


@router.get(
    "/model-info",
    response_model=ModelInfoResponse,
    summary="Get model configuration info",
)
async def get_model_info():
    """Returns current model configuration."""
    try:
        return ModelInfoResponse(
            service_provider=settings.LLM_PROVIDER,
            embedding_model=settings.EMBEDDING_MODEL,
            stream=settings.STREAM,
            top_k=settings.TOP_K,
            alpha=settings.ALPHA,
            temperature=settings.TEMPERATURE,
        )
    except Exception as e:
        logger.error(f"Error retrieving model info: {e}")
        raise ChatGenerationException("Failed to retrieve model configuration.")
