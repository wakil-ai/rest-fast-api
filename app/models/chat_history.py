from typing import List, Optional, Any
from datetime import datetime
from pydantic import BaseModel, Field, ConfigDict, field_serializer
from pydantic_core import core_schema
from bson import ObjectId


class PyObjectId(ObjectId):
    @classmethod
    def __get_pydantic_core_schema__(
        cls, source_type: Any, handler
    ) -> core_schema.CoreSchema:
        return core_schema.no_info_plain_validator_function(
            cls.validate,
            serialization=core_schema.to_string_ser_schema(),
        )

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

    model_config = ConfigDict(
        populate_by_name=True,
        arbitrary_types_allowed=True,
    )

    @field_serializer('id')
    def serialize_id(self, value):
        return str(value) if value else None


class ChatFeedback(BaseModel):
    id: Optional[PyObjectId] = Field(alias="_id", default=None)
    chat_id: str
    message_id: str
    feedback_type: str
    comment: Optional[str] = None
    timestamp: datetime = Field(default_factory=datetime.utcnow)

    model_config = ConfigDict(
        populate_by_name=True,
        arbitrary_types_allowed=True,
    )

    @field_serializer('id')
    def serialize_id(self, value):
        return str(value) if value else None


class CreateConversationRequest(BaseModel):
    session_id: str
    title: Optional[str] = "New Chat"


class AddMessageRequest(BaseModel):
    message: ChatMessage


class UpdateConversationRequest(BaseModel):
    title: str = Field(..., min_length=1, max_length=200, description="New conversation title")


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