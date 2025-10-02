from typing import List, Optional, Any, Dict
from pydantic import BaseModel, Field

class CreateUserRequest(BaseModel):
    user_id: str = Field(..., description="Telegram ID (as string)")
    username: Optional[str] = None
    first_name: Optional[str] = None
    last_name: Optional[str] = None
    picture: Optional[str] = None

class AddSessionRequest(BaseModel):
    user_id: str
    session_id: Optional[str] = None
    title: Optional[str] = "New chat"
    tags: List[str] = []

class AddMessageContent(BaseModel):
    query: Optional[str] = None
    response: Optional[str] = None
    
class AddMessageRequest(BaseModel):
    user_id: str
    session_id: str
    message_id: str
    content: AddMessageContent
    metadata: Optional[Dict[str, Any]] = None

class GetSessionsRequest(BaseModel):
    user_id: str
    limit: int = Field(50, ge=1, le=200)

class GetMessagesRequest(BaseModel):
    user_id: str
    session_id: str
    limit: int = Field(100, ge=1, le=500)