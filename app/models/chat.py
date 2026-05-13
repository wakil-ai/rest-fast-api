from enum import Enum

from pydantic import BaseModel, Field

from app.core.assistants import AssistantConfig
from app.core.config import settings


class ChatModel(str, Enum):
    """Supported chat models."""

    # OpenAI models
    GPT_4_1 = "gpt-4.1"
    GPT_4_1_MINI = "gpt-4.1-mini"
    GPT_4O = "gpt-4o"
    GPT_4O_MINI = "gpt-4o-mini"
    GPT_5_2 = "gpt-5.2"
    GPT_5_2_MINI = "gpt-5.2-mini"
    # Claude models
    CLAUDE_OPUS_4_5 = "claude-opus-4-5-20251101"
    CLAUDE_SONNET_4_5 = "claude-sonnet-4-5-20250929"
    CLAUDE_HAIKU_4_5 = "claude-haiku-4-5-20251001"
    # WakilAI models
    GPT_OSS_120B = "gpt-oss-120b"
    GEMMA_3_27B = "gemma-3-27b"
    # Gemini models
    GEMINI_3_PRO_PREVIEW = "gemini-3.1-pro-preview"
    GEMINI_3_FLASH_PREVIEW = "gemini-3-flash-preview"
    # Friendly short names — resolved to the actual preview/GA model id by
    # ``app.llms.gemini.resolve_gemini_model_name`` so callers don't need to track
    # which suffix Google currently exposes.
    GEMINI_3_1_PRO = "gemini-3.1-pro"
    GEMINI_3_1_FLASH = "gemini-3.1-flash"
    GEMINI_3_PRO = "gemini-3-pro"
    GEMINI_3_FLASH = "gemini-3-flash"


class AssistantType(str, Enum):
    """Supported assistant types."""

    MAIN = "main"
    DEEPRESEARCH = "deepresearch"
    DEEP_RESEARCH = "deep_research"
    COURT = "court"
    ADMINISTRATIVE_COURT = "administrative_court"
    ADMINISTRATIVE_COURT_LEGACY = "mamuriy_sud"
    TAX = "tax"
    TAX_LEGACY = "soliq"
    CONTRACT_ANALYZER = "contract_analyzer"
    CONTRACT_ANALYZER_LEGACY = "shartnoma"
    CRIMINAL_COURT = "criminal_court"
    CIVIL_COURT = "civil_court"
    ECONOMIC_COURT = "economic_court"

    @classmethod
    def get_available_types(cls):
        """Get all available assistant types from configuration."""
        return AssistantConfig.get_assistant_names()


class ChatRequest(BaseModel):
    """
    Request body for chat questions.
    """

    user_id: str = Field(
        ..., example="user_12345", description="Unique identifier for the user"
    )
    session_id: str = Field(..., description="Existing session identifier")
    query: str = Field(..., example="What are the marriage laws in Uzbekistan?")
    stream: bool | None = Field(
        default=settings.STREAM, description="Whether to stream the response"
    )
    assistant: AssistantType | None = Field(
        default=AssistantType.MAIN,
        description="Assistant type (use `court` for court matters; legacy aliases like soliq/shartnoma/mamuriy_sud are still supported)",
    )
    file_ids: list[str] | None = Field(
        default=None, description="Optional list of file IDs to use as context"
    )
    file_context: str | None = Field(
        default=None,
        description="Optional inline document or excerpt text to include as context for this turn",
    )
    project_id: str | None = Field(
        default=None,
        description=(
            "Optional legal project: links the session to this project on first use, "
            "retrieves Milvus `project_files` by this id, and stores `project_id` on the message"
        ),
    )


class ChatResponse(BaseModel):
    """
    Response body for chat answers.
    """

    answer: str
    session_id: str = Field(..., description="Session identifier for the chat")
    message_id: str = Field(
        ..., description="Message identifier assigned by the server"
    )
    latency_ms: int | None = Field(
        default=None,
        description="Server-side end-to-end generation latency in milliseconds",
    )
    attachments: list[dict[str, str]] | None = Field(
        default=None,
        description="Optional attachments (e.g., DOCX links)",
    )


class ModelInfoResponse(BaseModel):
    """
    Model configuration information.
    """

    top_k: int = Field(..., example=5, description="Number of top documents retrieved")
    alpha: float = Field(
        ..., example=0.8, description="Alpha value for relevance scoring"
    )
    temperature: float = Field(
        ..., example=0.1, description="Temperature setting for the LLM"
    )
    service_provider: str = Field(
        ..., example="novita", description="Service provider (novita or deepinfra)"
    )
    embedding_model: str = Field(
        ..., example="qwen", description="Embedding model (qwen or openai)"
    )
    stream: bool = Field(
        ..., example=False, description="Whether streaming responses are enabled"
    )


class AskFileRequest(BaseModel):
    """
    Request body for asking questions about uploaded files.
    """

    user_id: str = Field(
        ..., example="user_12345", description="Unique identifier for the user"
    )
    file_id: str = Field(
        ...,
        example="19e82083-61d4-45bb-b408-349a0fb2a237",
        description="ID of the uploaded file",
    )
    query: str = Field(
        ...,
        example="What is the main topic of this document?",
        description="Question about the file content",
    )
    stream: bool | None = Field(
        default=settings.STREAM, description="Whether to stream the response"
    )
    assistant: AssistantType | None = Field(
        default=AssistantType.MAIN,
        description="Assistant type (use `court` for court matters; legacy aliases like soliq/shartnoma/mamuriy_sud are still supported)",
    )
    model: ChatModel | None = Field(
        default=None, description="LLM model to use for generation"
    )


class AgenticRAGRequest(BaseModel):
    """
    Request body for streaming deep-research chat (``/chat/agent/stream``).

    Uses the orchestration graph with Tavily fallback when context is insufficient.
    """

    query: str = Field(
        ..., example="Explain the tax regulations for freelancers in Uzbekistan."
    )
    user_id: str = Field(..., description="User identifier")
    session_id: str = Field(..., description="Existing session identifier")
    project_id: str | None = Field(default=None, description="Optional project ID")
    file_ids: list[str] | None = Field(
        default=None, description="List of file IDs attached to the current message"
    )
    file_context: str | None = Field(
        default=None,
        description="Optional inline text context for this turn",
    )
