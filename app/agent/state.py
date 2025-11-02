from typing import Any, Dict, List, Optional
from pydantic import BaseModel, Field

class AgenticRAGState(BaseModel):
    """State management for legal QA workflow"""
    
    # Input parameters
    query: str = Field(default="", description="User's legal question")
    user_id: str = Field(default="", description="User identifier")
    session_id: str = Field(default="default", description="Session identifier")
    user_type: str = Field(default="lawyer", description="User type (lawyer, general, etc.)")
    language_instruction: str = Field(default="Respond in the same language as the question",description="Language instruction for response")
    enable_web_search: bool = Field(default=True, description="Flag to enable/disable web search fallback")
    enable_memory: bool = Field(default=True, description="Flag to enable/disable memory retrieval")
    
    # Intermediate outputs
    query_language: Optional[str] = Field(default=None, description="Detected language of the query")
    memory_output: Optional[Dict[str, Any]] = Field(default=None, description="Structured memory data")
    memory_docs: Optional[str] = Field(default=None, description="Formatted memory for context")
    retrieval_output: Optional[Dict[str, Any]] = Field(default=None, description="Retrieval metadata")
    retrieval_docs: Optional[str] = Field(default=None, description="Retrieved document content")
    web_search_output: Optional[Dict[str, Any]] = Field(default=None, description="Web search results")
    web_extraction_output: Optional[Dict[str, Any]] = Field(default=None, description="Web extraction results")
    
    # Final output
    answer: Optional[str] = Field(default=None, description="Generated answer")
    language_corrected_answer: Optional[str] = Field(default=None, description="Language-corrected final answer")
    
    # Error tracking
    errors: List[str] = Field(default_factory=list, description="Accumulated errors")

    class Config:
        arbitrary_types_allowed = True