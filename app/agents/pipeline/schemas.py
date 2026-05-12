from typing import Any

from pydantic import BaseModel, ConfigDict, Field


def normalize_assistant_name_for_registry(name: str | None) -> str:
    """Map strategy / legacy aliases to keys in PromptRegistry.ASSISTANT_MAPPING."""
    if not name:
        return "main"
    if name == "soliq":
        return "tax"
    if name == "umumiy":
        return "main"
    return name


class AgenticRAGState(BaseModel):
    """State management for legal QA workflow"""

    # Input parameters
    query: str = Field(default="", description="User's legal question")
    user_id: str = Field(default="", description="User identifier")
    session_id: str = Field(default="", description="Session identifier")
    message_id: str = Field(default="", description="Current message identifier")
    project_id: str | None = Field(default=None, description="Optional project ID")
    project_instructions: str | None = Field(
        default=None,
        description="User-defined project instructions appended after the main assistant prompt",
    )
    file_ids: list[str] | None = Field(
        default=None, description="List of file IDs attached to the current message"
    )

    # Intermediate outputs
    enriched_query: str | None = Field(
        default=None, description="Query enriched with memory context"
    )
    rewritten_query: str | None = Field(
        default=None, description="Rewritten query for retrieval with enrichments"
    )
    resolved_query: str | None = Field(
        default=None, description="Resolved query with context"
    )
    selected_assistant: str | None = Field(
        default=None, description="Selected assistant/collection (tax/main)"
    )
    memory_output: dict[str, Any] | None = Field(
        default=None, description="Structured memory data"
    )
    memory_docs: str | None = Field(
        default=None, description="Formatted memory for context"
    )
    retrieval_output: dict[str, Any] | None = Field(
        default=None, description="Retrieval metadata"
    )
    retrieval_docs: str | None = Field(
        default=None, description="Retrieved document content"
    )
    context_evaluation_output: dict[str, Any] | None = Field(
        default=None, description="Context sufficiency evaluation"
    )
    web_search_output: dict[str, Any] | None = Field(
        default=None, description="Web search results"
    )
    web_extraction_output: dict[str, Any] | None = Field(
        default=None, description="Web extraction results"
    )

    # Final output
    answer: str | None = Field(default=None, description="Generated answer")
    generation_meta: dict[str, Any] | None = Field(
        default=None,
        description="Non-sensitive generation metadata such as model and token usage",
    )
    attachments: list[dict[str, Any]] | None = Field(
        default=None,
        description="Attachments returned with the final answer",
    )

    # Error tracking
    errors: list[str] = Field(default_factory=list, description="Accumulated errors")

    model_config = ConfigDict(arbitrary_types_allowed=True)


class ChatPipelineState(AgenticRAGState):
    """Shared state for two-stage chat: context retrieval then final answer."""

    locked_assistant: str | None = Field(
        default=None,
        description="User-selected assistant; constrains routing when set (e.g. /chat/ask).",
    )
    requested_assistant: str | None = Field(
        default=None,
        description="Raw assistant id from the client before canonical routing aliases.",
    )
    stream: bool = Field(default=False, description="Whether last_agent streams tokens")
    answer_prompt_template: Any | None = Field(
        default=None,
        description="Optional PromptTemplate from specialist retrieve() for last_agent formatting",
    )
    classified_legal_intent: str | None = Field(
        default=None,
        description="Contract analyzer intent after retrieval, if applicable",
    )
    court_route_tag: str | None = Field(
        default=None,
        description="Administrative court route tag when court pipeline ran",
    )


class MemoryAgentResponse(BaseModel):
    """Response schema for memory summarizer agent."""

    session_notes: str = Field(
        default="", description="Summarized session conversation context"
    )
    personal_notes: str = Field(
        default="", description="Summarized user preferences and personal information"
    )
    resolved_query: str | None = Field(
        default=None,
        description="Query clarified with memory context, or None if no changes needed",
    )


class RetrievalStrategyResponse(BaseModel):
    """Response schema for retrieval specialist agent."""

    strategy: str = Field(
        description="Selected retrieval strategy: hybrid, dense, or sparse"
    )
    query_rewrite: str = Field(description="Optimized query for retrieval")
    query_translations: dict[str, str] = Field(
        default_factory=dict,
        description="Legacy field; pipeline retrieval uses query_rewrite only.",
    )
    assistant: str = Field(
        default="umumiy",
        description="Selected assistant/collection (supports legacy aliases)",
    )
    reasoning: str | None = Field(
        default=None, description="Explanation for strategy selection"
    )


class ContextEvaluationResponse(BaseModel):
    """Response schema for context evaluator agent."""

    is_sufficient: bool = Field(
        description="Whether the context is sufficient to answer the query"
    )
    reasoning: str = Field(description="Explanation for the sufficiency decision")
    missing_info: str | None = Field(
        default="", description="Description of missing information if insufficient"
    )


class WebSearchDocument(BaseModel):
    """Schema for a single web search document."""

    url: str = Field(description="Source URL of the document")
    title: str | None = Field(default=None, description="Document title")
    content: str = Field(description="Extracted and summarized content")


class WebSearchResponse(BaseModel):
    """Schema for web search summarizer agent."""

    docs: list[WebSearchDocument] = Field(
        default_factory=list,
        description="List of extracted and summarized web documents",
    )


class ProgressEventType(str):
    """Event types for progress streaming"""

    MEMORY_RETRIEVAL = "memory_retrieval"
    RETRIEVAL_STRATEGY = "retrieval_strategy"
    DOCUMENT_RETRIEVAL = "document_retrieval"
    CONTEXT_EVALUATION = "context_evaluation"
    WEB_SEARCH = "web_search"
    ANSWER_GENERATION = "answer_generation"
    COMPLETED = "completed"
    ERROR = "error"


class ProgressEvent(BaseModel):
    """Schema for progress events during agentic RAG execution."""

    event_type: str = Field(description="Type of progress event")
    status: str = Field(description="Status: in_progress, completed, failed")
    message: str = Field(description="User-friendly progress message")
    details: dict[str, Any] | None = Field(
        default=None, description="Additional non-sensitive details"
    )
