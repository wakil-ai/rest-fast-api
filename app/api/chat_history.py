from fastapi import APIRouter, status
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
    FeedbackResponse
)
from app.services.chat_history_service import ChatHistoryService
from app.utils.user_management import serialize_mongo_id, handle_service_error
from typing import List

router = APIRouter(prefix="/history", tags=["Chat History"])
chat_history_service = ChatHistoryService()

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

@router.get("/feedback/{user_id}/{session_id}/{message_id}", response_model=List[FeedbackResponse])
@handle_service_error
def get_feedback(user_id: str, session_id: str, message_id: str) -> List[FeedbackResponse]:
    """Retrieve feedback for a specific message."""
    feedbacks = chat_history_service.get_feedback(user_id=user_id, session_id=session_id, message_id=message_id)
    return [serialize_mongo_id(feedback) for feedback in feedbacks]