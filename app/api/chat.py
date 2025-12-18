# app/api/chat.py
import tempfile
import os
import uuid

from fastapi import APIRouter, HTTPException
from fastapi import UploadFile, File, Form
from fastapi.responses import StreamingResponse

from app.core.config import settings
from app.models.chat import ChatRequest, ChatResponse, ModelInfoResponse, AgenticRAGRequest, AssistantType
from app.services.chat_service import ChatService
from app.services.ocr_service import OCRService
from app.services.chat_history_service import ChatHistoryService
from app.orchestration.flow import AgenticRAGFlow
from app.utils.streaming import format_streaming_response, get_streaming_headers
from app.core.logger import logger

router = APIRouter(prefix="/chat", tags=["Chat"])

# Initialize services
agentic_rag_service = AgenticRAGFlow()
chat_service = ChatService()
ocr_service = OCRService()
chat_history_service = ChatHistoryService()

@router.post("/ask", summary="Ask a legal question")
async def ask_question(request: ChatRequest):
    """
    Ask a question related to Uzbek legal documents.
    Retrieves context from vector DB and generates an answer.
    Supports multiple assistants (main or soliq) via the assistant parameter.
    
    Response format automatically adapts based on STREAM config:
    """
    try:
        # Extract model name from enum if provided
        model_name = request.model.value if request.model else None
        
        # Determine collection based on assistant type
        collection_name = settings.MILVUS_SOLIQ_ASSISTANT_NAME if request.assistant == AssistantType.SOLIQ else settings.MILVUS_MAIN_NAME
        
        response = await chat_service.ask_question(
            user_id=request.user_id,
            query=request.query,
            chat_history=request.chat_history,
            stream=request.stream,
            collection_name=collection_name,
            model_name=model_name
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

@router.post("/soliq", summary="Ask a question to Soliq assistant (DEPRECATED - use /ask with assistant=soliq)")
async def ask_soliq_question(request: ChatRequest):
    """
    DEPRECATED: Use /ask endpoint with assistant parameter set to 'soliq' instead.
    
    Ask a question related to Uzbek tax/soliq documents.
    Retrieves context from the Soliq-specific vector DB collection and generates an answer.
    Always provides deep, detailed analysis regardless of user type.
    
    Response format automatically adapts based on STREAM config:
    """
    try:
        # Extract model name from enum if provided
        model_name = request.model.value if request.model else None
        
        response = await chat_service.ask_question(
            user_id=request.user_id,
            query=request.query,
            chat_history=request.chat_history,
            stream=request.stream,
            collection_name=settings.MILVUS_SOLIQ_ASSISTANT_NAME,
            model_name=model_name
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

@router.post("/file", response_model=ChatResponse)
async def ask_with_file(
    file: UploadFile = File(...),
    user_id: str = Form(...),
    query: str = Form(...),
    stream: bool = Form(False),
    assistant: str = Form("main"),
):
    """
    Upload a file and ask a question using the file's content as context.
    The number of retrieved documents (top_k) will be halved automatically when a file is sent.
    
    Parameters:
    - file: The file to upload (PDF, DOCX, images, etc.)
    - user_id: User ID
    - query: The question to ask about the file
    - stream: Whether to stream the response
    - assistant: Assistant type ('main' or 'soliq') - determines which collection to use
    """
    temp_file_path = None
    try:
        # Create temporary file
        with tempfile.NamedTemporaryFile(delete=False, suffix=f"_{file.filename}") as temp_file:
            content = await file.read()
            temp_file.write(content)
            temp_file_path = temp_file.name
        
        # Extract text from the file via OCR service
        ocr_result = await ocr_service.process_file(temp_file_path)
        
        # Determine collection based on assistant type
        collection_name = settings.MILVUS_SOLIQ_ASSISTANT_NAME if assistant == "soliq" else settings.MILVUS_MAIN_NAME
        
        response = await chat_service.ask_question(
            user_id=user_id,
            query=query,
            chat_history=[],
            stream=stream,
            file_context=ocr_result,
            collection_name=collection_name,
        )

        if stream:
            return StreamingResponse(
                format_streaming_response(response),
                media_type="text/event-stream",
                headers=get_streaming_headers()
            )
        else:
            # Return JSON response
            return ChatResponse(answer=response)

    except Exception as e:
        logger.error(f"[AskFileAPI] Error: {str(e)}")
        raise HTTPException(status_code=500, detail="Failed to process file and answer the question.")

    finally:
        if temp_file_path and os.path.exists(temp_file_path):
            os.remove(temp_file_path)

@router.post("/agent", summary="Ask a legal question via agentic RAG")
async def run_agentic_rag(request: AgenticRAGRequest) -> ChatResponse:
    """
    Execute legal QA flow end-to-end using agentic RAG approach.
    """
    logger.info(f"Starting Legal QA Flow for query: {request.query[:100]}...")
    
    # Build initial state
    initial_state = {
        "query": request.query,
        "user_id": request.user_id,
        "session_id": request.session_id,
        "enable_web_search": request.enable_web_search,
        "enable_memory": request.enable_memory,
    }
    
    # Execute flow
    answer = await agentic_rag_service.kickoff_async(initial_state)
    
    return ChatResponse(answer=answer)

@router.post("/agent/stream", summary="Stream legal question answer via agentic RAG")
async def stream_agentic_rag(request: AgenticRAGRequest) -> StreamingResponse:
    """
    Execute legal QA flow end-to-end using agentic RAG approach with streaming response.
    """
    logger.info(f"Starting Streaming Legal QA Flow for query: {request.query[:100]}...")
    
    # Build initial state
    initial_state = {
        "query": request.query,
        "user_id": request.user_id,
        "session_id": request.session_id,
        "enable_web_search": request.enable_web_search,
    }
    
    # Execute flow with streaming
    async def response_generator():
        answer = await agentic_rag_service.kickoff_async(initial_state)
        yield answer
        
    return StreamingResponse(
        format_streaming_response(response_generator()),
        media_type="text/event-stream",
        headers=get_streaming_headers()
    )