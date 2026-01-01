# app/api/chat.py
import asyncio

from fastapi import APIRouter, HTTPException
from fastapi.responses import StreamingResponse

from app.models.chat import ChatRequest, ChatResponse, ModelInfoResponse, AgenticRAGRequest, AssistantType, AskFileRequest
from app.services.chat_service import ChatService
from app.services.chat_history_service import ChatHistoryService
from app.services.rate_limit_service import RateLimitService
from app.orchestration.flow import AgenticRAGFlow
from app.utils.streaming import format_streaming_response, get_streaming_headers, format_progress_event
from app.core.config import settings
from app.core.logger import logger

router = APIRouter(prefix="/chat", tags=["Chat"])

# Initialize services
agentic_rag_service = AgenticRAGFlow()
chat_service = ChatService()
chat_history_service = ChatHistoryService()
rate_limit_service = RateLimitService()

@router.post("/ask", summary="Ask a legal question")
async def ask_question(request: ChatRequest):
    """
    Ask a question related to Uzbek legal documents.
    Retrieves context from vector DB and generates an answer.
    Supports multiple assistants (main or soliq) via the assistant parameter.
    
    Response format automatically adapts based on STREAM config:
    """
    try:
        # Determine assistant type for credit calculation
        assistant_type = "soliq" if request.assistant == AssistantType.SOLIQ else "main"
        
        # Check credit limit
        is_allowed, credits_remaining, limit = rate_limit_service.check_and_decrement_credits(request.user_id, assistant_type)
        if not is_allowed:
            raise HTTPException(
                status_code=429,
                detail=f"Insufficient credits. You have {credits_remaining}/{limit} credits remaining. This request requires {settings.CREDIT_COST_SOLIQ_ASSISTANT if assistant_type == 'soliq' else settings.CREDIT_COST_MAIN_ASSISTANT} credits."
            )
        
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
        # Check credit limit for soliq assistant
        is_allowed, credits_remaining, limit = rate_limit_service.check_and_decrement_credits(request.user_id, "soliq")
        if not is_allowed:
            raise HTTPException(
                status_code=429,
                detail=f"Insufficient credits. You have {credits_remaining}/{limit} credits remaining. This request requires {settings.CREDIT_COST_SOLIQ_ASSISTANT} credits."
            )
        
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


@router.post("/agent", summary="Ask a legal question via agentic RAG")
async def run_agentic_rag(request: AgenticRAGRequest) -> ChatResponse:
    """
    Execute legal QA flow end-to-end using agentic RAG approach.
    """
    # Check credit limit (deepresearch costs more credits)
    is_allowed, credits_remaining, limit = rate_limit_service.check_and_decrement_credits(request.user_id, "deepresearch")
    if not is_allowed:
        raise HTTPException(
            status_code=429,
            detail=f"Insufficient credits. You have {credits_remaining}/{limit} credits remaining. This request requires {settings.CREDIT_COST_DEEPRESEARCH} credits."
        )
    
    logger.info(f"Starting Legal QA Flow for query: {request.query[:100]}...")
    
    # Build initial state
    initial_state = {
        "query": request.query,
        "user_id": request.user_id,
        "session_id": request.session_id
    }
    
    # Execute flow
    answer = await agentic_rag_service.kickoff_async(initial_state)
    
    if settings.DEVELOPMENT_MODE:
        try:
            context = agentic_rag_service.state.retrieval_docs +  'Relevance Score: ' + str(agentic_rag_service.state.web_search_output)
        except Exception:
            context = "No retrieved contents available to show."
        return ChatResponse(answer=answer, retrieved_contents=context)
    else:
        return ChatResponse(answer=answer)

@router.post("/agent/stream", summary="Stream legal question answer via agentic RAG")
async def stream_agentic_rag(request: AgenticRAGRequest) -> StreamingResponse:
    """
    Execute legal QA flow end-to-end using agentic RAG approach with streaming response.
    
    Streams progress events for each step of the agentic RAG pipeline to provide
    real-time feedback to users while processing takes place.
    
    Event types streamed:
    - progress: Step-by-step progress updates (memory retrieval, document search, etc.)
    - chunk: Final answer character chunks
    - end: Stream completion signal
    """
    # Check credit limit (deepresearch costs more credits)
    is_allowed, credits_remaining, limit = rate_limit_service.check_and_decrement_credits(request.user_id, "deepresearch")
    if not is_allowed:
        raise HTTPException(
            status_code=429,
            detail=f"Insufficient credits. You have {credits_remaining}/{limit} credits remaining. This request requires {settings.CREDIT_COST_DEEPRESEARCH} credits."
        )
    
    # Queue for progress events
    progress_queue = asyncio.Queue()
    
    async def progress_callback(event: dict):
        """Callback to receive progress events from the flow."""
        await progress_queue.put(event)
    
    # Create flow instance with progress callback
    flow = AgenticRAGFlow(enable_progress_stream=True, progress_callback=progress_callback)
    
    # Build initial state
    initial_state = {
        "query": request.query,
        "user_id": request.user_id,
        "session_id": request.session_id
    }
    
    async def response_generator():
        """Generate streaming response with progress events and final answer."""
        # Start the flow execution in background
        flow_task = asyncio.create_task(flow.kickoff_async(initial_state))
        
        # Stream all events until flow completes AND queue is drained
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
                        "message": f"An error occurred: {str(e)}"
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