"""Pydantic schemas for agent responses."""

from typing import List, Optional
from pydantic import BaseModel, Field

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
