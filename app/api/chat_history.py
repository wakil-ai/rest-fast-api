from fastapi import APIRouter, status, HTTPException, UploadFile, File, Form
from app.models.chat_history import (
    UserCreateRequest,
    UserCreateResponse,
    SessionCreateResponse,
    SessionCreateRequest,
    SessionResponse,
    MessageCreateRequest,
    MessageResponse,
    MessageCreateResponse,
    FeedbackCreateRequest,
    FeedbackCreateResponse,
    FeedbackResponse,
    FileUploadResponse
)
from app.services.chat_history_service import ChatHistoryService
from app.services.ocr_service import OCRService
from app.services.storage_service import StorageService
from app.utils.user_management import serialize_mongo_id, handle_service_error
from app.core.logger import logger

from typing import List
import tempfile
import uuid
import datetime
import os


router = APIRouter(prefix="/history", tags=["Chat History"])

# Services used
chat_history_service = ChatHistoryService()
ocr_service = OCRService()
storage_service = StorageService()

def create_response(data: dict, message: str) -> dict:
    """Create a standardized API response."""
    return {"info": serialize_mongo_id(data), "message": message}

@router.post("/create/user/", status_code=status.HTTP_201_CREATED, response_model=UserCreateResponse)
@handle_service_error
def create_user(request: UserCreateRequest) -> UserCreateResponse:
    """Create a new user."""
    user_info = chat_history_service.create_user(
        user_id=request.user_id,
        username=request.username,
        first_name=request.first_name,
        last_name=request.last_name,
        picture=request.picture
    )
    return create_response(user_info, "User created successfully")

@router.post("/create/session/", status_code=status.HTTP_201_CREATED, response_model=SessionCreateResponse)
@handle_service_error
def create_session(request: SessionCreateRequest) -> SessionCreateResponse:
    """Create a new user session."""
    session_info = chat_history_service.create_session(
        user_id=request.user_id,
        session_id=request.session_id,
        title=request.title,
        tags=request.tags
    )
    return create_response(session_info, "Session created successfully")

@router.get("/sessions/{user_id}", response_model=List[SessionResponse])
@handle_service_error
def get_sessions(user_id: str, limit: int = 50) -> List[SessionResponse]:
    """Retrieve user sessions."""
    sessions = chat_history_service.get_sessions(user_id=user_id, limit=limit)
    return [serialize_mongo_id(session) for session in sessions]

@router.post("/edit/session/{session_id}", response_model=SessionResponse)
@handle_service_error
def edit_session(session_id: str, title: str = None, tags: List[str] = None) -> SessionResponse:
    """Edit a user session's title or tags."""
    session_info = chat_history_service.edit_session(
        session_id=session_id,
        title=title,
        tags=tags
    )
    return serialize_mongo_id(session_info)

@router.delete("/delete/session/{user_id}/{session_id}", status_code=status.HTTP_204_NO_CONTENT)
@handle_service_error
def delete_session(user_id: str, session_id: str) -> None:
    """Delete a user session and its messages."""
    chat_history_service.delete_session(user_id=user_id, session_id=session_id)
    return 

@router.post("/add/message/", status_code=status.HTTP_201_CREATED, response_model=MessageCreateResponse)
@handle_service_error
def add_message(request: MessageCreateRequest) -> MessageCreateResponse:
    """Add a message to a user session."""
    message_info = chat_history_service.add_message(
        user_id=request.user_id,
        session_id=request.session_id,
        message_id=request.message_id,
        content=request.content,
        metadata=request.metadata
    )
    return create_response(message_info, "Message added successfully")

@router.get("/messages/{user_id}/{session_id}", response_model=List[MessageResponse])
@handle_service_error
def get_messages(user_id: str, session_id: str, limit: int = 100) -> List[MessageResponse]:
    """Retrieve messages from a user session."""
    messages = chat_history_service.get_messages(user_id=user_id, session_id=session_id, limit=limit)
    return [serialize_mongo_id(message) for message in messages]

@router.post("/submit/feedback/", status_code=status.HTTP_201_CREATED, response_model=FeedbackCreateResponse)
@handle_service_error
def submit_feedback(request: FeedbackCreateRequest) -> FeedbackCreateResponse:
    """Submit feedback for a message."""
    feedback_info = chat_history_service.submit_feedback(
        user_id=request.user_id,
        session_id=request.session_id,
        message_id=request.message_id,
        feedback_type=request.feedback_type,
        comments=request.comment
    )
    return create_response(feedback_info, "Feedback submitted successfully")


@router.post("/create/file/{user_id}", status_code=status.HTTP_201_CREATED, response_model=FileUploadResponse)
async def create_file_upload(user_id: str, file: UploadFile = File(...)) -> FileUploadResponse:
    temp_file_path = None
    try:
        # Read file content
        content = await file.read()
        
        # Create temporary file for OCR processing
        with tempfile.NamedTemporaryFile(delete=False, suffix=f"_{file.filename}") as temp_file:
            temp_file.write(content)
            temp_file_path = temp_file.name
        
        # Process file with OCR
        ocr_result = await ocr_service.process_file(temp_file_path)

        # Generate unique file ID
        file_id = str(uuid.uuid4())
        
        # Upload file to Google Cloud Storage
        gcs_path = storage_service.generate_file_path(user_id, file_id, file.filename)
        file_url = storage_service.upload_file(
            file_content=content,
            destination_path=gcs_path,
            content_type=file.content_type or "application/octet-stream"
        )

        # Create file metadata
        file_metadata = {
            "file_name": file.filename,
            "file_type": file.content_type or "application/octet-stream",
            "file_size": len(content),
            "gcs_path": gcs_path
        }

        # Save to database
        chat_history_service.add_file_upload(
            user_id=user_id,
            file_id=file_id,
            file_url=file_url,
            ocr_result=ocr_result,
            file_metadata=file_metadata
        )

        return FileUploadResponse(
            user_id=user_id,
            file_id=file_id,
            file_url=file_url,
            file_metadata=file_metadata,
            ocr_result=ocr_result,
            created_at=datetime.datetime.utcnow(),
            updated_at=datetime.datetime.utcnow()
        )

    except Exception as e:
        logger.error(f"Error creating file upload: {str(e)}")
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=f"Failed to create file upload: {str(e)}")
    
    finally:
        # Clean up temporary file
        if temp_file_path and os.path.exists(temp_file_path):
            try:
                os.remove(temp_file_path)
            except Exception as cleanup_error:
                logger.warning(f"Failed to cleanup temp file: {str(cleanup_error)}")


@router.get("/files/{user_id}/", response_model=List[FileUploadResponse])
@handle_service_error
def get_user_files(user_id: str, limit: int = 50) -> List[FileUploadResponse]:
    """Retrieve all files uploaded by a user."""
    files = chat_history_service.get_files(user_id=user_id, limit=limit)
    return [serialize_mongo_id(file) for file in files]

@router.get("/files/{user_id}/{file_id}", response_model=FileUploadResponse)
@handle_service_error
def get_file_upload(user_id: str, file_id: str) -> FileUploadResponse:
    """Retrieve a specific file by file_id."""
    file_record = chat_history_service.get_file_by_id(user_id=user_id, file_id=file_id)
    if not file_record:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"File with file_id {file_id} not found for user {user_id}"
        )
    return serialize_mongo_id(file_record)

@router.delete("/files/{user_id}/{file_id}", status_code=status.HTTP_204_NO_CONTENT)
@handle_service_error
def delete_file_upload(user_id: str, file_id: str) -> None:
    """Delete a file upload record and the file from Google Cloud Storage."""
    # Get file record to get the GCS path
    file_record = chat_history_service.get_file_by_id(user_id=user_id, file_id=file_id)
    
    if not file_record:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"File with file_id {file_id} not found for user {user_id}"
        )
    
    # Delete from Google Cloud Storage
    gcs_path = file_record.get("file_metadata", {}).get("gcs_path")
    if gcs_path:
        try:
            storage_service.delete_file(gcs_path)
            logger.info(f"Deleted file from GCS: {gcs_path}")
        except Exception as e:
            logger.warning(f"Failed to delete file from GCS: {str(e)}")
    
    # Delete from database
    chat_history_service.delete_file_upload(user_id=user_id, file_id=file_id)
    return 
