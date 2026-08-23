from fastapi import APIRouter, Request

from core.dependencies import get_chat_service
from core.exceptions import ChatGenerationException
from core.logger import logger
from models.chat import (
    AgenticRAGRequest,
    ChatRequest,
    ModelInfoResponse,
    PromptEnhanceRequest,
    PromptEnhanceResponse,
)

router = APIRouter(prefix="/chat", tags=["Chat"])

chat_service = get_chat_service()


@router.post("/ask", summary="Ask a legal question")
async def ask_question(request: ChatRequest, raw_request: Request):
    """
    Ask a question related to Uzbek legal documents.
    Retrieves context from vector DB and generates an answer (all assistants).
    """
    return await chat_service.handle_chat_ask(request, raw_request)


@router.post("/agent/stream", summary="Stream deep-research legal answer")
async def stream_agentic_rag(request: AgenticRAGRequest):
    """
    Stream a deep-research answer through the orchestration graph.

    Returns session metadata followed by the final answer when generation completes.
    """
    return await chat_service.handle_agentic_rag_stream(request)


@router.post(
    "/prompt/enhance",
    response_model=PromptEnhanceResponse,
    summary="Improve a draft prompt before sending",
)
async def enhance_prompt(request: PromptEnhanceRequest):
    """
    Rewrite the draft in the composer into a clearer, more answerable question,
    tailored to the assistant it is aimed at. Does not send anything.
    """
    return await chat_service.handle_prompt_enhance(request)


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
