from fastapi import APIRouter, Request

from app.core.dependencies import get_chat_service
from app.core.exceptions import ChatGenerationException
from app.core.logger import logger
from app.models.chat import AgenticRAGRequest, ChatRequest, ModelInfoResponse

router = APIRouter(prefix="/chat", tags=["Chat"])

chat_service = get_chat_service()


@router.post("/ask", summary="Ask a legal question")
async def ask_question(request: ChatRequest, raw_request: Request):
    """
    Ask a question related to Uzbek legal documents.
    Retrieves context from vector DB and generates an answer (all assistants).
    """
    return await chat_service.handle_chat_ask(request, raw_request)


@router.post("/agent/stream", summary="Stream legal question answer via agentic RAG")
async def stream_agentic_rag(request: AgenticRAGRequest):
    """
    Execute legal QA flow using agentic RAG with streaming response.

    Streams progress events for each pipeline step:
    - progress: Step-by-step updates
    - chunk: Final answer characters
    - end: Stream completion
    """
    return await chat_service.handle_agentic_rag_stream(request)


@router.get("/assistants", summary="Get available assistants")
async def get_assistants():
    """Returns information about available assistants."""
    try:
        return chat_service.get_public_assistants_payload()
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
        return chat_service.get_model_info_response()
    except Exception as e:
        logger.error(f"Error retrieving model info: {e}")
        raise ChatGenerationException("Failed to retrieve model configuration.")
