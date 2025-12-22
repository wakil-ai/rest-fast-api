# app/api/chat.py
import tempfile
import os
import uuid

from fastapi import APIRouter, HTTPException
from fastapi import UploadFile, File, Form
from fastapi.responses import StreamingResponse

from app.core.config import settings
from app.models.chat import ChatRequest, ChatResponse, ModelInfoResponse, AgenticRAGRequest, AssistantType, AskFileRequest
from app.services.chat_service import ChatService
from app.services.chat_history_service import ChatHistoryService
from app.orchestration.flow import AgenticRAGFlow
from app.utils.streaming import format_streaming_response, get_streaming_headers
from app.core.logger import logger

router = APIRouter(prefix="/chat", tags=["Chat"])

# Initialize services
agentic_rag_service = AgenticRAGFlow()
chat_service = ChatService()
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
            # Handle development mode with debug data
            if settings.DEVELOPMENT_MODE and isinstance(response, tuple):
                answer, debug_data = response
                return ChatResponse(
                    answer=answer,
                    retrieved_contents=debug_data.get("retrieved_contents"),
                    logs=debug_data.get("logs")
                )
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
            # Handle development mode with debug data
            if settings.DEVELOPMENT_MODE and isinstance(response, tuple):
                answer, debug_data = response
                return ChatResponse(
                    answer=answer,
                    retrieved_contents=debug_data.get("retrieved_contents"),
                    logs=debug_data.get("logs")
                )
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
async def ask_with_file(request: AskFileRequest):
    """
    Ask a question about a previously uploaded file using its file_id.
    The file content (OCR result) is retrieved from the database.
    
    Parameters:
    - user_id: User ID
    - file_id: ID of the previously uploaded file
    - query: The question to ask about the file
    - stream: Whether to stream the response
    - assistant: Assistant type ('main' or 'soliq') - determines which collection to use
    - model: Optional model to use for generation
    """
    try:
        # Fetch file record from database
        file_record = chat_history_service.get_file_by_id(user_id=request.user_id, file_id=request.file_id)
        
        if not file_record:
            raise HTTPException(
                status_code=404,
                detail=f"File with file_id {request.file_id} not found for user {request.user_id}"
            )
        
        # Get OCR result from the file record (already processed during upload)
        ocr_result = file_record.get("ocr_result", "")
        
        if not ocr_result:
            logger.warning(f"No OCR result found for file {request.file_id}")
            raise HTTPException(
                status_code=400,
                detail="File does not have processed content. Please re-upload the file."
            )
        
        # Extract model name from enum if provided
        model_name = request.model.value if request.model else None
        
        # Determine collection based on assistant type
        collection_name = settings.MILVUS_SOLIQ_ASSISTANT_NAME if request.assistant == AssistantType.SOLIQ else settings.MILVUS_MAIN_NAME
        
        logger.info(f"Processing file query for file_id: {request.file_id}, user: {request.user_id}")
        
        response = await chat_service.ask_question(
            user_id=request.user_id,
            query=request.query,
            chat_history=[],
            stream=request.stream,
            file_context=ocr_result,
            collection_name=collection_name,
            model_name=model_name
        )

        if request.stream:
            return StreamingResponse(
                format_streaming_response(response),
                media_type="text/event-stream",
                headers=get_streaming_headers()
            )
        else:
            # Handle development mode with debug data
            if settings.DEVELOPMENT_MODE and isinstance(response, tuple):
                answer, debug_data = response
                return ChatResponse(
                    answer=answer,
                    retrieved_contents=debug_data.get("retrieved_contents"),
                    logs=debug_data.get("logs")
                )
            # Return JSON response
            return ChatResponse(answer=response)

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"[AskFileAPI] Error: {str(e)}")
        raise HTTPException(status_code=500, detail="Failed to process file query. Please try again.")

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
        "session_id": request.session_id
    }
    
    # Execute flow
    answer = await agentic_rag_service.kickoff_async(initial_state)
    
    return ChatResponse(answer=answer)

@router.post("/agent/stream", summary="Stream legal question answer via agentic RAG")
async def stream_agentic_rag(request: AgenticRAGRequest) -> StreamingResponse:
    """
    Execute legal QA flow end-to-end using agentic RAG approach with streaming response.
    
    Note: CrewAI Flow streaming works by setting stream=True on the Flow class itself,
    which enables streaming for all Crew executions within the flow.
    """
    logger.info(f"Starting Streaming Legal QA Flow for query: {request.query[:100]}...")
    
    # Create flow instance with streaming enabled
    flow = AgenticRAGFlow()
    
    # Build initial state
    initial_state = {
        "query": request.query,
        "user_id": request.user_id,
        "session_id": request.session_id
    }
    
    async def response_generator():
        try:
            # For CrewAI flows, streaming needs to be set as class attribute
            # The flow will automatically stream crew outputs
            streaming = await flow.kickoff_async(initial_state)

            # Check if streaming is actually available
            if hasattr(streaming, '__aiter__'):
                async for chunk in streaming:
                    # Stream the content from crews
                    if hasattr(chunk, 'content'):
                        yield chunk.content
                    else:
                        yield str(chunk)
            else:
                # If no streaming available, return the final result
                yield str(streaming)

        except Exception as e:
            logger.error("Streaming error", exc_info=True)
            yield {"error": str(e)}

    return StreamingResponse(
        format_streaming_response(response_generator()),
        media_type="text/event-stream",
        headers=get_streaming_headers(),
    )