from enum import Enum

from pydantic import BaseModel, Field

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


class MessagePair(BaseModel):
    """
    A question-answer pair in the chat history.
    """

    question: str
    answer: str


class AssistantType(str, Enum):
    """Supported assistant types."""

    MAIN = "main"
    SOLIQ = "soliq"
    MAMURIY_SUD = "mamuriy_sud"
    SHARTNOMA = "shartnoma"

    @classmethod
    def get_available_types(cls):
        """Get all available assistant types from configuration."""
        from app.core.assistants import AssistantConfig

        return AssistantConfig.get_assistant_names()


class ChatRequest(BaseModel):
    """
    Request body for chat questions.
    """

    user_id: str = Field(
        ..., example="user_12345", description="Unique identifier for the user"
    )
    query: str = Field(..., example="What are the marriage laws in Uzbekistan?")
    chat_history: list[MessagePair] | None = Field(
        default=None, description="Previous question-answer pairs"
    )
    stream: bool | None = Field(
        default=settings.STREAM, description="Whether to stream the response"
    )
    model: ChatModel | None = Field(
        default=None, description="LLM model to use for generation"
    )
    assistant: AssistantType | None = Field(
        default=AssistantType.MAIN, description="Assistant type: main or soliq"
    )
    file_ids: list[str] | None = Field(
        default=None, description="Optional list of file IDs to use as context"
    )


class ChatResponse(BaseModel):
    """
    Response body for chat answers.
    """

    answer: str
    retrieved_contents: str | None = Field(
        default=None, description="Retrieved documents (dev mode only)"
    )

    attachments: list[dict[str, str]] | None = Field(
        default=None,
        description="Optional attachments (e.g., shartnoma docx links)",
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
        default=AssistantType.MAIN, description="Assistant type: main or soliq"
    )
    model: ChatModel | None = Field(
        default=None, description="LLM model to use for generation"
    )


class AgenticRAGRequest(BaseModel):
    """
    Request body for agentic RAG questions.
    """

    query: str = Field(
        ..., example="Explain the tax regulations for freelancers in Uzbekistan."
    )
    user_id: str = Field(default="user_123", description="User identifier")
    session_id: str = Field(default="default", description="Session identifier")
    project_id: str | None = Field(default=None, description="Optional project ID")
    file_ids: list[str] | None = Field(
        default=None, description="List of file IDs attached to the current message"
    )
