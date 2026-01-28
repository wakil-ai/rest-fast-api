import asyncio

from fastapi import APIRouter, HTTPException
from fastapi.responses import StreamingResponse

from app.core.assistants import AssistantConfig
from app.core.config import settings
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
from app.orchestration.flow import AgenticRAGFlow
from app.services.chat_service import ChatService
from app.utils.streaming import format_streaming_response, get_streaming_headers

router = APIRouter(prefix="/chat", tags=["Chat"])

chat_service = ChatService()


@router.post("/ask", summary="Ask a legal question")
async def ask_question(request: ChatRequest):
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

        model_name = request.model.value if request.model else None
        should_stream = request.stream or settings.STREAM

        response = await chat_service.ask_question(
            user_id=request.user_id,
            query=request.query,
            chat_history=request.chat_history,
            stream=should_stream,
            file_ids=request.file_ids,
            assistant=assistant_name,
            model_name=model_name,
        )

        if should_stream:
            return chat_service.create_streaming_response(response)

        if settings.DEVELOPMENT_MODE and isinstance(response, tuple):
            answer, debug_data = response
            return ChatResponse(
                answer=answer,
                retrieved_contents=debug_data.get("retrieved_contents"),
            )

        return ChatResponse(answer=response)

    except ChatException:
        raise
    except HTTPException:
        raise
    except Exception as e:
        logger.error(
            f"[ChatAPI] Unexpected error in ask_question: {str(e)}", exc_info=True
        )
        raise ChatGenerationException()


@router.post("/agent", summary="Ask a legal question via agentic RAG")
async def run_agentic_rag(request: AgenticRAGRequest) -> ChatResponse:
    """Execute legal QA flow using agentic RAG approach."""
    try:
        chat_service.validate_query_length(request.query)

        await chat_service.verify_user_credits(
            user_id=request.user_id,
            assistant_type="deepresearch",
            required_credits=settings.CREDIT_COST_DEEPRESEARCH,
        )

        flow_service = AgenticRAGFlow()
        initial_state = chat_service.build_agentic_state(request)

        logger.info(f"Starting Legal QA Flow for query: {request.query[:100]}...")

        answer = await flow_service.kickoff_async(initial_state)
        debug_context = chat_service.extract_debug_context(flow_service)

        return chat_service.create_response(answer, debug_context)

    except ChatException:
        raise
    except Exception as e:
        logger.error(
            f"[AgenticRAG] Unexpected error in run_agentic_rag: {str(e)}", exc_info=True
        )
        raise FlowExecutionException()


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

        progress_queue = asyncio.Queue()

        async def progress_callback(event: dict):
            await progress_queue.put(event)

        flow = AgenticRAGFlow(
            enable_progress_stream=True, progress_callback=progress_callback
        )

        initial_state = chat_service.build_agentic_state(request)

        async def response_generator():
            flow_task = asyncio.create_task(flow.kickoff_async(initial_state))

            flow_complete = False
            while not (flow_complete and progress_queue.empty()):
                # Check if flow is done
                if flow_task.done() and not flow_complete:
                    flow_complete = True
                    # Await the result to catch any exceptions
                    try:
                        await flow_task
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
        assistants = AssistantConfig.get_assistants()
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
