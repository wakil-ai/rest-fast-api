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
