# app/api/chat.py

from fastapi import APIRouter, HTTPException
from fastapi.responses import StreamingResponse
from app.core.config import settings
from app.models.chat import ChatRequest, ChatResponse, ModelInfoResponse
from app.services.chat_service import ChatService
from app.utils.streaming import format_streaming_response, get_streaming_headers

from app.core.logger import logger

router = APIRouter(prefix="/chat", tags=["Chat"])

# Initialize service
chat_service = ChatService()

@router.post("/ask", summary="Ask a legal question")
async def ask_question(request: ChatRequest):
    """
    Ask a question related to Uzbek legal documents.
    Retrieves context from vector DB and generates an answer.
    
    Response format automatically adapts based on STREAM config:
    """
    try:
        response = await chat_service.ask_question(
            user_id=request.user_id,
            query=request.query,
            is_lawyer=request.is_lawyer,
            chat_history=request.chat_history,
            stream=request.stream
        )
        
        if request.stream:
            # Return streaming response
            return StreamingResponse(
                format_streaming_response(response),
                media_type="text/event-stream",
                headers=get_streaming_headers()
            )
        else:
            # Return JSON response
            return ChatResponse(answer=response)

    except Exception as e:
        logger.error(f"[ChatAPI] Error: {str(e)}")
        raise HTTPException(
            status_code=500, 
            detail="Failed to generate answer. Please try again later."
        )

@router.post("/soliq", summary="Ask a question to Soliq assistant")
async def ask_soliq_question(request: ChatRequest):
    """
    Ask a question related to Uzbek tax/soliq documents.
    Retrieves context from the Soliq-specific vector DB collection and generates an answer.
    Always provides deep, detailed analysis regardless of user type.
    
    Response format automatically adapts based on STREAM config:
    """
    try:
        response = await chat_service.ask_question(
            user_id=request.user_id,
            query=request.query,
            is_lawyer=True,  # Always use deep analysis for Soliq assistant
            chat_history=request.chat_history,
            stream=request.stream,
            collection_name=settings.MILVUS_SOLIQ_ASSISTANT_NAME
        )
        
        if request.stream:
            # Return streaming response
            return StreamingResponse(
                format_streaming_response(response),
                media_type="text/event-stream",
                headers=get_streaming_headers()
            )
        else:
            # Return JSON response
            return ChatResponse(answer=response)

    except Exception as e:
        logger.error(f"[ChatAPI] Error in Soliq endpoint: {str(e)}")
        raise HTTPException(
            status_code=500, 
            detail="Failed to generate answer. Please try again later."
        )

@router.get("/model-info", response_model=ModelInfoResponse, summary="Get model configuration info")
async def get_model_info():
    """
    Returns information about the current model configuration.
    """
    return ModelInfoResponse(
        service_provider=settings.LLM_PROVIDER,
        embedding_model=settings.EMBEDDING_MODEL,
        stream=settings.STREAM,
        top_k=settings.TOP_K,
        alpha=settings.ALPHA,
        temperature=settings.TEMPERATURE,
    )
