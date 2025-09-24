from typing import List, Optional
from datetime import datetime
from pydantic import BaseModel, Field
from bson import ObjectId


class PyObjectId(ObjectId):
    @classmethod
    def __get_validators__(cls):
        yield cls.validate

    @classmethod
    def validate(cls, v):
        if not ObjectId.is_valid(v):
            raise ValueError("Invalid ObjectId")
        return ObjectId(v)

    @classmethod
    def __get_pydantic_json_schema__(cls, core_schema, handler):
        return {'type': 'string'}




class MessageMetadata(BaseModel):
    latency: Optional[int] = None


class ChatMessage(BaseModel):
    type: str
    content: str
    timestamp: str
    message_id: str
    metadata: Optional[MessageMetadata] = None


class ChatConversation(BaseModel):
    id: Optional[PyObjectId] = Field(alias="_id", default=None)
    chat_id: str
    session_id: str
    title: str = "New Chat"
    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)
    messages: List[ChatMessage] = Field(default_factory=list)
    total_messages: int = 0
    status: str = "active"

    class Config:
        populate_by_name = True
        arbitrary_types_allowed = True
        json_encoders = {ObjectId: str}


class ChatFeedback(BaseModel):
    id: Optional[PyObjectId] = Field(alias="_id", default=None)
    session_id: str
    message_id: str
    feedback_type: str
    comment: Optional[str] = None
    timestamp: datetime = Field(default_factory=datetime.utcnow)

    class Config:
        populate_by_name = True
        arbitrary_types_allowed = True
        json_encoders = {ObjectId: str}


class CreateConversationRequest(BaseModel):
    session_id: str
    title: Optional[str] = "New Chat"


class AddMessageRequest(BaseModel):
    message: ChatMessage


class CreateFeedbackRequest(BaseModel):
    chat_id: str
    message_id: str
    feedback_type: str
    comment: Optional[str] = None


class ConversationResponse(BaseModel):
    chat_id: str
    session_id: str
    title: str
    messages: List[ChatMessage]
    total_messages: int
    created_at: datetime
    updated_at: datetime


class ConversationListItem(BaseModel):
    chat_id: str
    title: str
    created_at: datetime
    updated_at: datetime
    total_messages: int
    last_message_preview: Optional[str] = None


class ConversationListResponse(BaseModel):
    conversations: List[ConversationListItem]
    total_count: int


class FeedbackResponse(BaseModel):
    message: str = "Feedback submitted successfully"
    feedback_id: str