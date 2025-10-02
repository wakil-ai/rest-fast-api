from fastapi import APIRouter, HTTPException, status, Request
from typing import List

from app.models.chat_history import (
    CreateUserRequest,
    AddSessionRequest,
    AddMessageRequest,
)
from app.services.chat_history_service import ChatHistoryService
from pydantic import BaseModel
from app.core.logger import logger

router = APIRouter(prefix="/history", tags=["Chat History"])
chat_history_service = ChatHistoryService()

@router.post("/create/user/", status_code=status.HTTP_201_CREATED)
def create_user(request: CreateUserRequest) -> dict:
    """Create a new user."""
    try:
        user_information = chat_history_service.create_user(
            user_id=request.user_id,
            username=request.username,
            first_name=request.first_name,
            last_name=request.last_name,
            picture=request.picture
        )
        # sanitize for JSON response
        if "_id" in user_information:
            user_information["_id"] = str(user_information["_id"])
        return {
            "info": user_information,
            "message": "User created successfully"
        }
    except Exception as e:
        logger.error(f"Error creating user: {e}")
        raise HTTPException(status_code=500, detail="Internal Server Error")
    
@router.post("/create/session/", status_code=status.HTTP_201_CREATED)
def create_session(request: AddSessionRequest) -> dict:
    """Create a new user session."""
    try:
        session_information = chat_history_service.create_session(
            user_id=request.user_id,
            sessions_id=request.session_id,
            title=request.title,
            tags=request.tags
        )
        if "_id" in session_information:
            session_information["_id"] = str(session_information["_id"])
        return {"info": session_information, "message": "Session created successfully"}
    except Exception as e:
        logger.error(f"Error creating session: {e}")
        raise HTTPException(status_code=500, detail="Internal Server Error")
    
@router.get("/sessions/{user_id}", response_model=List[dict])
def get_sessions(user_id: str) -> List[dict]:
    """Retrieve user sessions."""
    try:
        sessions = chat_history_service.get_sessions(user_id=user_id)
        
        for session in sessions:
            if "_id" in session:
                session["_id"] = str(session["_id"])
        
        return sessions
    except Exception as e:
        logger.error(f"Error retrieving sessions: {e}")
        raise HTTPException(status_code=500, detail="Internal Server Error")
    
@router.post("/add/message/", status_code=status.HTTP_201_CREATED)
def add_message(request: AddMessageRequest) -> dict:
    """Add a message to a user session."""
    try:
        message_information = chat_history_service.add_message(
            user_id=request.user_id,
            session_id=request.session_id,
            message_id=request.message_id,
            content=request.content,
            metadata=request.metadata
        )
        if "_id" in message_information:
            message_information["_id"] = str(message_information["_id"])
        return {"info": message_information, "message": "Message added successfully"}
    except Exception as e:
        logger.error(f"Error adding message: {e}")
        raise HTTPException(status_code=500, detail="Internal Server Error")
    
@router.get("/messages/{user_id}/{session_id}", response_model=List[dict])
def get_messages(user_id: str, session_id: str) -> List[dict]:
    """Retrieve messages from a user session."""
    try:
        messages = chat_history_service.get_messages(user_id=user_id, session_id=session_id)
        
        for message in messages:
            if "_id" in message:
                message["_id"] = str(message["_id"])
                
        return messages
    except Exception as e:
        logger.error(f"Error retrieving messages: {e}")
        raise HTTPException(status_code=500, detail="Internal Server Error")