from fastapi import APIRouter, HTTPException, status, UploadFile, Body, Form, File

from app.core.logger import logger
from app.models.chat_history import (
    FeedbackCreateRequest,
    FeedbackCreateResponse,
    FileUploadResponse,
    MessageCreateRequest,
    MessageCreateResponse,
    MessageResponse,
    ProjectCreateRequest,
    ProjectCreateResponse,
    ProjectResponse,
    SessionCreateRequest,
    SessionCreateResponse,
    SessionResponse,
    UserCreateRequest,
    UserCreateResponse,
)
from app.services.chat_history_service import ChatHistoryService
from app.services.file_management import FileManager
from app.services.storage_service import StorageService
from app.utils.user_management import handle_service_error, serialize_mongo_id

router = APIRouter(prefix="/history", tags=["Chat History"])

# Services used
chat_history_service = ChatHistoryService()
file_manager = FileManager()
storage_service = StorageService()


def create_response(data: dict, message: str) -> dict:
    """Create a standardized API response."""
    return {"info": serialize_mongo_id(data), "message": message}


# USER MANAGEMENT


@router.post(
    "/users",
    status_code=status.HTTP_201_CREATED,
    response_model=UserCreateResponse,
)
@handle_service_error
def create_user(request: UserCreateRequest) -> UserCreateResponse:
    """Create a new user."""
    user_info = chat_history_service.create_user(
        user_id=request.user_id,
        username=request.username,
        first_name=request.first_name,
        last_name=request.last_name,
        picture=request.picture,
    )
    return create_response(user_info, "User created successfully")


@router.get("/users/{user_id}", response_model=UserCreateResponse)
@handle_service_error
def get_user(user_id: str) -> UserCreateResponse:
    """Get user by user_id."""
    user = chat_history_service.get_user(user_id=user_id)
    if not user:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail=f"User {user_id} not found"
        )
    return create_response(user, "User retrieved successfully")


# PROJECT MANAGEMENT


@router.post(
    "/projects",
    status_code=status.HTTP_201_CREATED,
    response_model=ProjectCreateResponse,
)
@handle_service_error
def create_project(request: ProjectCreateRequest) -> ProjectCreateResponse:
    """Create a new project."""
    project_info = chat_history_service.create_project(
        user_id=request.user_id,
        project_id=request.project_id,
        title=request.title,
    )
    return create_response(project_info, "Project created successfully")


@router.get("/projects/{user_id}", response_model=list[ProjectResponse])
@handle_service_error
def get_projects(user_id: str, limit: int = 50) -> list[ProjectResponse]:
    """Retrieve all projects for a user."""
    projects = chat_history_service.get_projects(user_id=user_id, limit=limit)
    return [serialize_mongo_id(project) for project in projects]


@router.get("/projects/{user_id}/{project_id}", response_model=ProjectResponse)
@handle_service_error
def get_project(user_id: str, project_id: str) -> ProjectResponse:
    """Get a specific project."""
    project = chat_history_service.get_project(project_id=project_id)
    if not project:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Project {project_id} not found",
        )
    if project.get("user_id") != user_id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN, detail="Access denied"
        )
    return serialize_mongo_id(project)


@router.patch("/projects/{project_id}", response_model=ProjectResponse)
@handle_service_error
def edit_project(project_id: str, title: str) -> ProjectResponse:
    """Edit a project's title."""
    project_info = chat_history_service.edit_project(project_id=project_id, title=title)
    return serialize_mongo_id(project_info)


@router.delete(
    "/projects/{user_id}/{project_id}", status_code=status.HTTP_204_NO_CONTENT
)
@handle_service_error
def delete_project(user_id: str, project_id: str) -> None:
    """Delete a project and all its associated data."""
    chat_history_service.delete_project(user_id=user_id, project_id=project_id)
    return


# SESSION MANAGEMENT


@router.post(
    "/sessions",
    status_code=status.HTTP_201_CREATED,
    response_model=SessionCreateResponse,
)
@handle_service_error
def create_session(request: SessionCreateRequest) -> SessionCreateResponse:
    """Create a new chat session."""
    session_info = chat_history_service.create_session(
        user_id=request.user_id,
        session_id=request.session_id,
        project_id=request.project_id,
        title=request.title,
        tags=request.tags,
    )
    return create_response(session_info, "Session created successfully")


@router.get("/sessions/{user_id}", response_model=list[SessionResponse])
@handle_service_error
def get_sessions(
    user_id: str, project_id: str | None = None, limit: int = 50
) -> list[SessionResponse]:
    """Retrieve user sessions, optionally filtered by project."""
    sessions = chat_history_service.get_sessions(
        user_id=user_id, project_id=project_id, limit=limit
    )
    return [serialize_mongo_id(session) for session in sessions]


@router.get("/sessions/{user_id}/{session_id}", response_model=SessionResponse)
@handle_service_error
def get_session(user_id: str, session_id: str) -> SessionResponse:
    """Get a specific session."""
    session = chat_history_service.get_session(session_id=session_id)
    if not session:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Session {session_id} not found",
        )
    if session.get("user_id") != user_id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN, detail="Access denied"
        )
    return serialize_mongo_id(session)


@router.patch("/sessions/{session_id}", response_model=SessionResponse)
@handle_service_error
def edit_session(
    session_id: str, title: str | None = None, tags: list[str] | None = None
) -> SessionResponse:
    """Edit a session's title or tags."""
    session_info = chat_history_service.edit_session(
        session_id=session_id, title=title, tags=tags
    )
    return serialize_mongo_id(session_info)


@router.delete("/sessions/{session_id}", status_code=status.HTTP_204_NO_CONTENT)
@handle_service_error
def delete_session(session_id: str) -> None:
    """Delete a session and its messages."""
    chat_history_service.delete_session(session_id=session_id)
    return


# MESSAGE MANAGEMENT


@router.post(
    "/messages",
    status_code=status.HTTP_201_CREATED,
    response_model=MessageCreateResponse,
)
@handle_service_error
def add_message(request: MessageCreateRequest) -> MessageCreateResponse:
    """Add a message to a session. Only session_id is required."""
    # Ensure the session exists (some clients post messages before explicitly creating a session).
    if not chat_history_service.get_session(session_id=request.session_id):
        user_id = (
            request.user_id
            or (request.metadata or {}).get("user_id")
            or (request.metadata or {}).get("userId")
            or "anonymous"
        )
        chat_history_service.create_session(
            user_id=user_id, session_id=request.session_id
        )

    message_info = chat_history_service.add_message(
        session_id=request.session_id,
        message_id=request.message_id,
        content=request.content,
        metadata=request.metadata,
    )
    return create_response(message_info, "Message added successfully")


@router.get("/messages/{session_id}", response_model=list[MessageResponse])
@handle_service_error
def get_messages(session_id: str, limit: int = 100) -> list[MessageResponse]:
    """Retrieve messages from a session. Only session_id is required."""
    messages = chat_history_service.get_messages(session_id=session_id, limit=limit)
    return [serialize_mongo_id(message) for message in messages]


@router.get("/messages/{session_id}/{message_id}", response_model=MessageResponse)
@handle_service_error
def get_message(session_id: str, message_id: str) -> MessageResponse:
    """Get a specific message."""
    message = chat_history_service.get_message(message_id=message_id)
    if not message:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Message {message_id} not found",
        )
    if message.get("session_id") != session_id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Message does not belong to this session",
        )
    return serialize_mongo_id(message)


# FEEDBACK MANAGEMENT


@router.post(
    "/feedback",
    status_code=status.HTTP_201_CREATED,
    response_model=FeedbackCreateResponse,
)
@handle_service_error
def submit_feedback(request: FeedbackCreateRequest) -> FeedbackCreateResponse:
    """Submit feedback for a message. Only message_id is required."""
    feedback_info = chat_history_service.submit_feedback(
        message_id=request.message_id,
        feedback_type=request.feedback_type,
        comments=request.comments,
    )
    return create_response(feedback_info, "Feedback submitted successfully")


@router.get("/feedback/{message_id}")
@handle_service_error
def get_feedback(message_id: str):
    """Retrieve feedback for a message. Only message_id is required."""
    feedback = chat_history_service.get_feedback(message_id=message_id)
    return create_response(feedback, "Feedback retrieved")


# FILE MANAGEMENT
@router.post(
    "/upload/files/project/{project_id}",
    status_code=status.HTTP_201_CREATED,
    response_model=FileUploadResponse,
)
async def upload_file(
    project_id: str,
    file: UploadFile = File(...),
    user_id: str = Form(...),
) -> FileUploadResponse:
    """
    Upload file to project with OCR processing and storage
    """
    # FileManager.upload_file_to_project requires `user_id`; accept it via multipart form
    status_code, result = await file_manager.upload_file_to_project(
        file=file, project_id=project_id, user_id=user_id
    )

    if status_code == 200:
        return result

    if status_code == 404:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=result)

    # 500 + unexpected cases
    raise HTTPException(
        status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        detail=f"Failed to upload file: {result}",
    )


@router.get("/files/project/{project_id}", response_model=list[FileUploadResponse])
@handle_service_error
def get_project_files(project_id: str, limit: int = 100) -> list[FileUploadResponse]:
    """Retrieve all files for a project."""
    files = chat_history_service.get_files(project_id=project_id, limit=limit)
    return [serialize_mongo_id(file) for file in files]


@router.get("/files/{file_id}", response_model=FileUploadResponse)
@handle_service_error
def get_file_upload(file_id: str) -> FileUploadResponse:
    """Retrieve a specific file by file_id."""
    file_record = chat_history_service.get_file_by_id(file_id=file_id)
    if not file_record:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"File {file_id} not found",
        )
    return serialize_mongo_id(file_record)


@router.get("/files/{file_id}/view")
@handle_service_error
def get_file_view_url(file_id: str, expiration_minutes: int = 60) -> dict:
    """
    Get a temporary signed URL to view/download a file.
    Returns a signed URL that expires after the specified time (default: 60 minutes).
    """
    # Get file record from database
    file_record = chat_history_service.get_file_by_id(file_id=file_id)

    if not file_record:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"File {file_id} not found",
        )

    # Get the GCS path from file metadata
    gcs_path = file_record.get("file_metadata", {}).get("gcs_path")
    if not gcs_path:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="File path not found in metadata",
        )

    try:
        # Generate a temporary signed URL
        signed_url = storage_service.get_signed_url(
            gcs_path, expiration_minutes=expiration_minutes
        )

        return {
            "file_id": file_id,
            "file_name": file_record.get("file_metadata", {}).get("file_name"),
            "view_url": signed_url,
            "expires_in_minutes": expiration_minutes,
            "content_type": file_record.get("file_metadata", {}).get("file_type"),
        }
    except Exception as e:
        logger.error(f"Failed to generate signed URL for file {file_id}: {str(e)}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to generate file access URL",
        )


@router.patch("/files/{file_id}/status")
@handle_service_error
def update_file_status(
    file_id: str, status: str, ocr_result: str | None = None
) -> FileUploadResponse:
    """Update file processing status."""
    file_record = chat_history_service.update_file_status(
        file_id=file_id, status=status, ocr_result=ocr_result
    )
    return serialize_mongo_id(file_record)


@router.delete("/files/{file_id}", status_code=status.HTTP_204_NO_CONTENT)
@handle_service_error
def delete_file_upload(file_id: str) -> None:
    """Delete a file upload record and the file from Google Cloud Storage."""
    # Get file record to get the GCS path
    file_record = chat_history_service.get_file_by_id(file_id=file_id)

    if not file_record:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"File {file_id} not found",
        )

    # Delete from Google Cloud Storage (archives the file)
    gcs_path = file_record.get("file_metadata", {}).get("gcs_path")
    if gcs_path:
        try:
            storage_service.delete_file(gcs_path)
            logger.info(f"Archived file from GCS: {gcs_path}")
        except Exception as e:
            logger.warning(f"Failed to archive file from GCS: {str(e)}")

    # Delete from database
    chat_history_service.delete_file_upload(file_id=file_id)
    return


@router.post("/upload/files/message", summary="Upload a file")
async def upload_file_message(
    file: UploadFile = File(...),
    user_id: str = Form(...),
) -> FileUploadResponse:
    """
    Upload a file to be associated with a message later.
    """
    status, response = await file_manager.upload_message_file(
        file=file,
        user_id=user_id,
    )

    if status != 200:
        raise HTTPException(status_code=status, detail=response)

    # Return the full FileUploadResponse model so frontend gets all metadata
    return response


@router.patch("/files/{file_id}/message", summary="Associate a file with a message")
async def associate_file_with_message(
    file_id: str,
    message_id: str = Body(..., embed=True),
):
    """
    Associate an uploaded file with a message.
    """
    try:
        chat_history_service.update_file_message_id(file_id, message_id)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))

    return {"message": "File associated with message successfully"}


@router.get("/files/user/{user_id}", summary="List user's uploaded files")
async def list_user_files(user_id: str):
    """
    Retrieve a list of files uploaded by a specific user.
    """
    files = chat_history_service.get_files_by_user(user_id)
    return files


@router.get("/files/message/{message_id}", summary="Get file processing status")
async def get_message_file_status(message_id: str):
    """
    Get the processing status of files associated with a specific message.
    """
    files = chat_history_service.get_files_by_message(message_id)
    return files


@router.get("/sync/{user_id}")
@handle_service_error
def sync_user_data(user_id: str, months: int = 3):
    """
    Get all sessions and messages for a user for the last N months.
    Used for bulk synchronization to client.
    """
    data = chat_history_service.get_sync_data(user_id=user_id, months=months)

    # Serialize mongo IDs
    data["sessions"] = [serialize_mongo_id(s) for s in data["sessions"]]

    # Serialize messages map
    # MessageResponse serializer expected a list, but here we have a dict of lists
    # We need to manually serialize the list of dicts
    serialized_messages = {}
    for session_id, msgs in data["messages"].items():
        serialized_messages[session_id] = [serialize_mongo_id(m) for m in msgs]

    data["messages"] = serialized_messages

    return data
