from fastapi import APIRouter, HTTPException, status, Request
from typing import List
from app.models.chat_history import (
    CreateUserRequest,
    AddSessionRequest,
    AddMessageRequest,
)
from app.services.chat_history_service import ChatHistoryService
from app.core.logger import logger
from app.utils.user_management import serialize_mongo_id, handle_service_error

router = APIRouter(prefix="/history", tags=["Chat History"])
chat_history_service = ChatHistoryService()

def create_response(data: dict, message: str) -> dict:
    """Create a standardized API response."""
    return {"info": serialize_mongo_id(data), "message": message}

@router.post("/create/user/", status_code=status.HTTP_201_CREATED)
@handle_service_error
def create_user(request: CreateUserRequest) -> dict:
    """Create a new user."""
    user_info = chat_history_service.create_user(
        user_id=request.user_id,
        username=request.username,
        first_name=request.first_name,
        last_name=request.last_name,
        picture=request.picture
    )
    return create_response(user_info, "User created successfully")

@router.post("/create/session/", status_code=status.HTTP_201_CREATED)
@handle_service_error
def create_session(request: AddSessionRequest) -> dict:
    """Create a new user session."""
    session_info = chat_history_service.create_session(
        user_id=request.user_id,
        session_id=request.session_id,
        title=request.title,
        tags=request.tags
    )
    return create_response(session_info, "Session created successfully")

@router.get("/sessions/{user_id}", response_model=List[dict])
@handle_service_error
def get_sessions(user_id: str) -> List[dict]:
    """Retrieve user sessions."""
    sessions = chat_history_service.get_sessions(user_id=user_id)
    return [serialize_mongo_id(session) for session in sessions]

@router.post("/add/message/", status_code=status.HTTP_201_CREATED)
@handle_service_error
def add_message(request: AddMessageRequest) -> dict:
    """Add a message to a user session."""
    message_info = chat_history_service.add_message(
        user_id=request.user_id,
        session_id=request.session_id,
        message_id=request.message_id,
        content=request.content,
        metadata=request.metadata
    )
    return create_response(message_info, "Message added successfully")

@router.get("/messages/{user_id}/{session_id}", response_model=List[dict])
@handle_service_error
def get_messages(user_id: str, session_id: str) -> List[dict]:
    """Retrieve messages from a user session."""
    messages = chat_history_service.get_messages(user_id=user_id, session_id=session_id)
    return [serialize_mongo_id(message) for message in messages]