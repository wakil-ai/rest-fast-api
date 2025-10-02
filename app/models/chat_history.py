from typing import List, Optional, Any, Dict
from datetime import datetime
from pydantic import BaseModel, Field
from enum import Enum

# USER MODELS
class CreateUserRequest(BaseModel):
    user_id: str = Field(..., description="Telegram ID (as string)")
    username: Optional[str] = None
    first_name: Optional[str] = None
    last_name: Optional[str] = None
    is_lawyer: bool = Field(default=False)
    picture: Optional[str] = None


class UserInfoResponse(BaseModel):
    """Response model for user creation/update"""
    mongo_id: str = Field(..., description="MongoDB ObjectId")
    user_id: str = Field(..., description="Telegram user ID")
    username: Optional[str] = None
    first_name: Optional[str] = None
    last_name: Optional[str] = None
    picture: Optional[str] = None
    is_lawyer: bool = Field(default=False)
    created_at: datetime
    updated_at: datetime


class CreateUserResponse(BaseModel):
    """Standardized response for user creation"""
    info: UserInfoResponse
    message: str = Field(default="User created successfully")


# SESSION MODELS
class SessionInfoResponse(BaseModel):
    """Response model for session data"""
    mongo_id: str = Field(..., description="MongoDB ObjectId")
    user_id: str = Field(..., description="User ID")
    session_id: str = Field(..., description="Session UUID")
    title: str = Field(default="New Chat")
    tags: List[str] = Field(default_factory=list)
    created_at: datetime
    updated_at: datetime


class CreateSessionResponse(BaseModel):
    """Standardized response for session creation"""
    info: SessionInfoResponse
    message: str = Field(default="Session created successfully")


class AddSessionRequest(BaseModel):
    user_id: str
    session_id: Optional[str] = None
    title: Optional[str] = "New chat"
    tags: List[str] = []


# MESSAGE MODELS
class MessageContentResponse(BaseModel):
    """Message content structure"""
    query: Optional[str] = None
    response: Optional[str] = None


class MessageInfoResponse(BaseModel):
    """Response model for message data"""
    mongo_id: str = Field(..., description="MongoDB ObjectId")
    user_id: str = Field(..., description="User ID")
    session_id: str = Field(..., description="Session ID")
    message_id: str = Field(..., description="Message UUID")
    content: MessageContentResponse
    metadata: Optional[Dict[str, Any]] = None
    created_at: datetime
    updated_at: datetime


class AddMessageContent(BaseModel):
    query: Optional[str] = None
    response: Optional[str] = None


class AddMessageRequest(BaseModel):
    user_id: str
    session_id: str
    message_id: str
    content: AddMessageContent
    metadata: Optional[Dict[str, Any]] = None


class GetMessagesRequest(BaseModel):
    """Get messages request model"""
    user_id: str
    session_id: str
    limit: int = Field(100, ge=1, le=500)


# FEEDBACK MODELS
class FeedbackType(str, Enum):
    """Feedback type enumeration"""
    LIKE = "like"
    DISLIKE = "dislike"


class FeedbackInfoResponse(BaseModel):
    """Response model for feedback data"""
    mongo_id: str = Field(..., description="MongoDB ObjectId")
    user_id: str = Field(..., description="User ID")
    session_id: str = Field(..., description="Session ID")
    message_id: str = Field(..., description="Message ID")
    feedback_type: FeedbackType = Field(..., description="Type of feedback")
    comment: Optional[str] = None
    created_at: datetime
    updated_at: datetime


class SubmitFeedbackRequest(BaseModel):
    """Request model for submitting message feedback"""
    user_id: str = Field(..., description="User ID who is giving feedback")
    session_id: str = Field(..., description="Session ID containing the message")
    message_id: str = Field(..., description="Message ID being rated")
    feedback_type: FeedbackType = Field(..., description="Type of feedback: like or dislike")
    comment: Optional[str] = Field(None, max_length=500, description="Optional feedback comment")


class SubmitFeedbackResponse(BaseModel):
    """Standardized response for feedback submission"""
    info: FeedbackInfoResponse
    message: str = Field(default="Feedback submitted successfully")


class GetFeedbackRequest(BaseModel):
    """Request model for getting feedback for a message"""
    user_id: str
    session_id: str
    message_id: str