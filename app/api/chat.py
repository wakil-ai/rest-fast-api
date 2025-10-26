# app/api/chat.py

from fastapi import APIRouter, HTTPException
from fastapi import UploadFile, File, Form
from fastapi.responses import StreamingResponse
from app.core.config import settings
from app.models.chat import ChatRequest, ChatResponse, ModelInfoResponse
from app.services.chat_service import ChatService
from app.services.ocr_service import OCRService
from app.utils.streaming import format_streaming_response, get_streaming_headers


from app.core.logger import logger

router = APIRouter(prefix="/chat", tags=["Chat"])

# Initialize service
chat_service = ChatService()
ocr_service = OCRService()

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
    is_lawyer: bool = Form(False),
    stream: bool = Form(False),
):
    """
    Upload a file and ask a question using the file's content as context.
    The number of retrieved documents (top_k) will be halved automatically when a file is sent.
    """
    try:
        # Extract text from the file via OCR service
        ocr_result = ocr_service.process_file(file.file, file.filename)
        
        # Combine per-page lines into one big text block
        combined_text = ocr_result.get("combined_text") or "\n".join(
            ["\n".join(p.get("lines", [])) for p in ocr_result.get("text_by_page", [])]
        )

        response = await chat_service.ask_question(
            user_id=user_id,
            query=query,
            is_lawyer=is_lawyer,
            chat_history=[],
            stream=stream,
            file_context=combined_text,
        )

        # If streaming requested, return error (this endpoint returns final JSON for now)
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

from app.agent.crew import crew
@router.post("/agent", summary="Ask a legal question via agentic RAG")
async def ask_question_via_agent(request: ChatRequest):
    """
    Ask a question using the agentic RAG approach.
    The agent will decide how to retrieve context and generate the answer.
    """
    try:
        response = await crew.kickoff_async(
            inputs={
                "user_id": request.user_id,
                "session_id": "default",
                "user_type": "lawyer",
                "language_instruction": "Uzbek Latin",
                "query": request.query,
                "context": "",
                "chat_history": "",
            },
        )
        return ChatResponse(answer=response.raw)

    except Exception as e:
        logger.error(f"[ChatAgentAPI] Error: {str(e)}")
        raise HTTPException(
            status_code=500, 
            detail="Failed to generate answer via agent. Please try again later."
        )