# app/orchestration/schemas.py

from typing import List, Optional, Any, Dict
from pydantic import BaseModel, Field

class AgenticRAGState(BaseModel):
    """State management for legal QA workflow"""
    
    # Input parameters
    query: str = Field(default="", description="User's legal question")
    user_id: str = Field(default="", description="User identifier")
    session_id: str = Field(default="default", description="Session identifier")
    
    # Intermediate outputs
    enriched_query: Optional[str] = Field(default=None, description="Query enriched with memory context")
    rewritten_query: Optional[str] = Field(default=None, description="Rewritten query for retrieval with enrichments")
    resolved_query: Optional[str] = Field(default=None, description="Resolved query with context")
    selected_assistant: Optional[str] = Field(default=None, description="Selected assistant/collection (soliq/umumiy)")
    memory_output: Optional[Dict[str, Any]] = Field(default=None, description="Structured memory data")
    memory_docs: Optional[str] = Field(default=None, description="Formatted memory for context")
    retrieval_output: Optional[Dict[str, Any]] = Field(default=None, description="Retrieval metadata")
    retrieval_docs: Optional[str] = Field(default=None, description="Retrieved document content")
    context_evaluation_output: Optional[Dict[str, Any]] = Field(default=None, description="Context sufficiency evaluation")
    web_search_output: Optional[Dict[str, Any]] = Field(default=None, description="Web search results")
    web_extraction_output: Optional[Dict[str, Any]] = Field(default=None, description="Web extraction results")
    
    # Final output
    answer: Optional[str] = Field(default=None, description="Generated answer")
    
    # Error tracking
    errors: List[str] = Field(default_factory=list, description="Accumulated errors")

    class Config:
        arbitrary_types_allowed = True

class MemoryAgentResponse(BaseModel):
    """Response schema for memory summarizer agent."""
    session_notes: str = Field(default="", description="Summarized session conversation context")
    personal_notes: str = Field(default="", description="Summarized user preferences and personal information")
    resolved_query: Optional[str] = Field(default=None, description="Query clarified with memory context, or None if no changes needed")

class RetrievalStrategyResponse(BaseModel):
    """Response schema for retrieval specialist agent.""" 
    strategy: str = Field(description="Selected retrieval strategy: hybrid, dense, sparse, or specific")
    query_rewrite: str = Field(description="Optimized query for retrieval")
    assistant: str = Field(default="umumiy", description="Selected assistant/collection: soliq or umumiy")
    reasoning: Optional[str] = Field(default=None, description="Explanation for strategy selection")

class ContextEvaluationResponse(BaseModel):
    """Response schema for context evaluator agent."""
    is_sufficient: bool = Field(description="Whether the context is sufficient to answer the query")
    reasoning: str = Field(description="Explanation for the sufficiency decision")
    missing_info: Optional[str] = Field(default="", description="Description of missing information if insufficient")

class WebSearchDocument(BaseModel):
    """Schema for a single web search document."""
    url: str = Field(description="Source URL of the document")
    title: Optional[str] = Field(default=None,description="Document title")
    content: str = Field(description="Extracted and summarized content")

class WebSearchResponse(BaseModel):
    """Response schema for web search summarizer agent."""
    docs: List[WebSearchDocument] = Field(default_factory=list,description="List of extracted and summarized web documents")
