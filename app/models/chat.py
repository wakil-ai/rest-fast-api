# app/models/chat.py

from typing import List, Optional
from pydantic import BaseModel, Field
from app.core.config import settings

class MessagePair(BaseModel):
    """
    A question-answer pair in the chat history.
    """
    question: str
    answer: str

class ChatRequest(BaseModel):
    """
    Request body for chat questions.
    """
    user_id: str = Field(..., example="user_12345", description="Unique identifier for the user")
    query: str = Field(..., example="What are the marriage laws in Uzbekistan?")
    chat_history: Optional[List[MessagePair]] = Field(default=None, description="Previous question-answer pairs")
    stream: Optional[bool] = Field(default=settings.STREAM, description="Whether to stream the response")

class ChatResponse(BaseModel):
    """
    Response body for chat answers.
    """
    answer: str

class ModelInfoResponse(BaseModel):
    """
    Model configuration information.
    """
    top_k: int = Field(..., example=5, description="Number of top documents retrieved")
    alpha: float = Field(..., example=0.8, description="Alpha value for relevance scoring")
    temperature: float = Field(..., example=0.1, description="Temperature setting for the LLM")
    service_provider: str = Field(..., example="novita", description="Service provider (novita or deepinfra)")
    embedding_model: str = Field(..., example="qwen", description="Embedding model (qwen or openai)")
    stream: bool = Field(..., example=False, description="Whether streaming responses are enabled")

class AgenticRAGRequest(BaseModel):
    """
    Request body for agentic RAG questions.
    """
    query: str = Field(..., example="Explain the tax regulations for freelancers in Uzbekistan.")
    user_id: str = Field(default="user_123", description="User identifier")
    session_id: str = Field(default="default", description="Session identifier")
    enable_web_search: bool = Field(default=True, description="Flag to enable/disable web search fallback")
    enable_memory: bool = Field(default=True, description="Flag to enable/disable memory retrieval")