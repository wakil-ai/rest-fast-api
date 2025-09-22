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
    llm_type: str = Field(..., example="gemma", description="Type of model (gpt or gemma)")
    query: str = Field(..., example="What are the marriage laws in Uzbekistan?")
    top_k: int = Field(default=settings.TOP_K, example=5)
    chat_history: Optional[List[MessagePair]] = Field(default=None, description="Previous question-answer pairs")

class ChatResponse(BaseModel):
    """
    Response body for chat answers.
    """
    answer: str

class ModelInfoResponse(BaseModel):
    """
    Model configuration information.
    """
    available_models: List[str] = Field(..., example=["gpt", "gemma"], description="Available models (gpt or gemma)")
    service_provider: str = Field(..., example="novita", description="Service provider (novita or deepinfra)")
    embedding_model: str = Field(..., example="qwen", description="Embedding model (qwen or openai)")
    stream: bool = Field(..., example=False, description="Whether streaming responses are enabled")
