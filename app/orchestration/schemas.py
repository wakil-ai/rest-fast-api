# app/orchestration/schemas.py

from typing import List, Optional, Any, Dict
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
    enriched_query: Optional[str] = Field(default=None, description="Query enriched with memory context")
    rewritten_query: Optional[str] = Field(default=None, description="Rewritten query for retrieval with enrichments")
    query_language: Optional[str] = Field(default=None, description="Detected language of the query")
    resolved_query: Optional[str] = Field(default=None, description="Resolved query with context")
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

class MemoryAgentResponse(BaseModel):
    """Response schema for memory summarizer agent."""
    session_notes: str = Field(default="", description="Summarized session conversation context")
    personal_notes: str = Field(default="", description="Summarized user preferences and personal information")
    resolved_query: str = Field(description="Query clarified with memory context")

class RetrievalStrategyResponse(BaseModel):
    """Response schema for retrieval specialist agent.""" 
    strategy: str = Field(description="Selected retrieval strategy: hybrid, dense, sparse, or specific")
    query_rewrite: str = Field(description="Optimized query for retrieval")
    reasoning: Optional[str] = Field(default=None, description="Explanation for strategy selection")

class WebSearchDocument(BaseModel):
    """Schema for a single web search document."""
    url: str = Field(description="Source URL of the document")
    title: Optional[str] = Field(default=None,description="Document title")
    content: str = Field(description="Extracted and summarized content")

class WebSearchResponse(BaseModel):
    """Response schema for web search summarizer agent."""
    docs: List[WebSearchDocument] = Field(default_factory=list,description="List of extracted and summarized web documents")
