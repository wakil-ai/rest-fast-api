from enum import Enum

from pydantic import BaseModel, Field

from core.assistants import AssistantConfig
from core.config import settings


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
    # Friendly short names — map to actual Google model ids via deployment settings.
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


class ClarifyQuestion(BaseModel):
    """One multiple-choice question about the user's own situation."""

    id: str = Field(..., description="Stable snake_case key naming the fact")
    question: str = Field(..., description="The question, in the user's language")
    options: list[str] = Field(
        default_factory=list,
        description="Two to four choices. Clients always add a free-text option.",
    )


class PromptClarifyRequest(BaseModel):
    """Request body for draft clarification."""

    query: str = Field(
        ..., min_length=1, example="sudga bersam yutamanmi",
        description="The user's draft, as currently typed in the composer",
    )
    assistant: AssistantType | None = Field(
        default=AssistantType.MAIN,
        description="Assistant the draft is aimed at; selects which facts matter",
    )
    language: str | None = Field(
        default=None, description="UI locale (uz/ru/en); a tiebreaker only"
    )
    history: str | None = Field(
        default=None,
        description=(
            "Recent conversation turns. Mid-thread a draft is often a fragment that "
            "only makes sense against what came before, so without this the model has "
            "nothing to work with."
        ),
    )


class PromptClarifyResponse(BaseModel):
    """Questions to put to the user. An empty list means the draft is ready as-is."""

    questions: list[ClarifyQuestion] = Field(default_factory=list)
    assistant: str


class PromptEnhanceRequest(BaseModel):
    """Request body for composer draft enhancement."""

    query: str = Field(
        ...,
        min_length=1,
        example="qqs qancha",
        description="The user's draft, as currently typed in the composer",
    )
    assistant: AssistantType | None = Field(
        default=AssistantType.MAIN,
        description="Assistant the draft is aimed at; selects the enhancement hint",
    )
    language: str | None = Field(
        default=None,
        description="UI locale (uz/ru/en). A tiebreaker only — the draft's language wins.",
    )
    history: str | None = Field(
        default=None,
        description=(
            "Recent conversation turns. Mid-thread a draft is often a fragment that "
            "only makes sense against what came before, so without this the model has "
            "nothing to work with."
        ),
    )
    answers: dict[str, str] | None = Field(
        default=None,
        description="Facts the user supplied, keyed by clarify question id",
    )


class PromptEnhanceResponse(BaseModel):
    """Enhanced draft, with the original so clients can offer undo."""

    original: str
    enhanced: str
    assistant: str
    changed: bool


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
            "loads project/file context through the internal LLM service, and stores "
            "`project_id` on the message"
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

    top_k: int = Field(..., example=5, description="Default context search limit")
    alpha: float = Field(
        ..., example=0.0, description="Legacy compatibility field"
    )
    temperature: float = Field(
        ..., example=0.0, description="Legacy compatibility field"
    )
    service_provider: str = Field(
        ..., example="rest-api-llm", description="Internal AI service provider"
    )
    embedding_model: str = Field(
        ..., example="rest-api-llm", description="Internal AI service embedding label"
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

    Proxied to the internal LLM service for orchestration and optional web/retrieval work.
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


class TaskBriefRequest(BaseModel):
    """
    Request body for generating a brief AI summary of a task (``/chat/brief``).

    Lightweight one-shot call — no session, no credits, no streaming. The
    frontend fires it on the task landing page so the user sees a quick
    overview before starting a chat.
    """

    context: str = Field(
        ...,
        description="Structured context about the task (title, description, objective, state, etc.)",
    )
    language: str = Field(
        default="en",
        description="Language code for the response (en, ru, uz)",
    )


class TaskBriefResponse(BaseModel):
    """Response body for ``/chat/brief``."""

    brief: str = Field(..., description="A 2-3 sentence summary of the task")
