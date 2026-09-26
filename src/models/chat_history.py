from datetime import datetime
from enum import Enum
from typing import Any

from pydantic import BaseModel, Field, field_validator, model_validator


# User Models
class UserCreateRequest(BaseModel):
    user_id: str = Field(..., description="User ID (e.g., Telegram ID)")
    username: str | None = None
    first_name: str | None = None
    phone_number: str | None = None
    last_name: str | None = None
    picture: str | None = None
    # Meta ad attribution from the mobile apps. `Any` on purpose: a strict type makes
    # Pydantic 422 a malformed value; `normalize_ad_attribution` drops it instead.
    platform: Any = None
    madid: Any = None
    anon_id: Any = None
    att: Any = None
    os_version: Any = None
    app_version: Any = None
    app_build: Any = None


class UserUpdateRequest(BaseModel):
    field: str = Field(
        ..., description="Field to update (username, first_name, last_name, picture)"
    )
    value: str = Field(..., description="New value for the specified field")


class UserPhoneUpdateRequest(BaseModel):
    user_id: str = Field(..., description="User ID (stored as _id)")
    phone_number: str = Field(..., description="User phone number")


class UserResponse(BaseModel):
    """Response model for user data"""

    user_id: str = Field(..., description="User ID (stored as _id)", alias="_id")
    username: str | None = None
    first_name: str | None = None
    last_name: str | None = None
    picture: str | None = None
    phone_number: str | None = None
    is_blocked: bool | None = None
    blocked_at: datetime | None = None
    blocked_reason: str | None = None
    unblocked_at: datetime | None = None
    created_at: datetime
    updated_at: datetime
    model_config = {"populate_by_name": True}


class UserCreateResponse(BaseModel):
    """Standardized response for user operations"""

    info: UserResponse
    message: str = Field(default="User operation successful")


class AccountDeletionRequest(BaseModel):
    """Optional body for the account-deletion (archive) endpoint."""

    reason: str | None = Field(
        default=None,
        max_length=500,
        description="Optional free-text reason for the deletion request",
    )


class AccountDeletionResponse(BaseModel):
    """Result of an account-deletion (archive) request.

    Idempotent: a repeat call on an already-archived account returns 200 with
    ``already_archived=True`` rather than an error.
    """

    user_id: str = Field(..., description="The archived user's ID")
    status: str = Field(
        default="archived", description="Account status after the request"
    )
    already_archived: bool = Field(
        ...,
        description="True if the account was already archived before this request",
    )
    archived_at: datetime | None = Field(
        None, description="When the account was archived (UTC)"
    )
    message: str = Field(default="Account archived successfully")


# Session Models
class SessionStatus(str, Enum):
    draft = "draft"
    active = "active"


class SessionCreateRequest(BaseModel):
    user_id: str = Field(..., description="User ID who owns the session")
    title: str | None = Field("New Chat", description="Session title")
    tags: list[str] = Field(
        default_factory=list, description="Optional tags for categorization"
    )


class SessionResponse(BaseModel):
    """Response model for session data"""

    user_id: str = Field(..., description="User ID")
    session_id: str = Field(..., description="Session ID (stored as _id)", alias="_id")
    project_id: str | None = Field(
        default=None, description="Legal project scope when set"
    )
    title: str = Field(default="New Chat")
    tags: list[str] = Field(default_factory=list)
    status: SessionStatus = Field(default=SessionStatus.draft)
    activated_at: datetime | None = None
    created_at: datetime
    updated_at: datetime
    model_config = {"populate_by_name": True}


class SessionEditRequest(BaseModel):
    title: str | None = Field(None, description="New session title")
    tags: list[str] | None = Field(None, description="New tags for the session")


# Message Models
class MessageContent(BaseModel):
    """Message content structure (required for both user queries and assistant responses)"""

    query: str = Field(..., description="User query")
    response: str = Field(..., description="Assistant response")


class MessageCreateRequest(BaseModel):
    """Request model for creating a new message"""

    session_id: str = Field(..., description="Session ID (only field required)")
    content: MessageContent = Field(..., description="Message content")
    file_ids: list[str] | None = Field(
        None, description="Optional file ID if this message is a file attachment"
    )
    metadata: dict[str, Any] | None = Field(None, description="Optional metadata")


class MessageResponse(BaseModel):
    """Response model for message data"""

    session_id: str = Field(..., description="Session ID")
    message_id: str = Field(..., description="Message ID (stored as _id)", alias="_id")
    user_id: str | None = Field(
        ..., description="User ID (auto-populated from session)"
    )  # optional for backward compatibility
    file_ids: list[str] = Field(
        default_factory=list,
        description="File IDs attached to this message (saved with the message)",
    )
    content: MessageContent
    metadata: dict[str, Any] | None = None

    @field_validator("file_ids", mode="before")
    @classmethod
    def coerce_missing_file_ids(cls, v: Any) -> Any:
        return [] if v is None else v

    feedback_type: str | None = Field(
        None, description="Feedback type (positive/negative)"
    )
    feedback_content: str | None = Field(None, description="Optional feedback comment")
    created_at: datetime
    updated_at: datetime
    model_config = {"populate_by_name": True}


class MessageSharedResponse(BaseModel):
    """Response model for shared message data"""

    share_id: str = Field(..., description="Share ID (stored as _id)", alias="_id")
    message_id: str = Field(..., description="Message ID")
    url: str = Field(..., description="Public URL to access the shared message")
    created_at: datetime
    model_config = {"populate_by_name": True}


class MessageSharedRequest(BaseModel):
    user_id: str = Field(..., description="User ID who is sharing the message")


class ShareResponse(BaseModel):
    """Response model for shared message data"""

    share_id: str = Field(..., description="Share ID (stored as _id)", alias="_id")
    question: str = Field(..., description="Original user query")
    answer: str = Field(..., description="Original assistant response")
    assistant: str | None = Field(None, description="Assistant name/model if available")
    created_at: datetime
    model_config = {"populate_by_name": True}


class MessageCreateResponse(BaseModel):
    """Standardized response for message operations"""

    info: MessageResponse
    message: str = Field(default="Message operation successful")


# Feedback Models
class FeedbackCreateRequest(BaseModel):
    """Request model for submitting message feedback"""

    message_id: str = Field(..., description="Message ID")
    feedback_type: str = Field(
        ..., description="Type of feedback: positive or negative"
    )
    feedback_content: str | None = Field(
        None, max_length=500, description="Optional feedback comment"
    )


# File Models
class FilePublicMetadataResponse(BaseModel):
    """Safe subset of file metadata for clients (no storage paths, hashes, or OCR)."""

    file_name: str | None = Field(None, description="Original upload filename")
    file_type: str = Field(
        default="application/octet-stream",
        description="MIME type of the file",
    )
    file_size: int = Field(default=0, ge=0, description="Size in bytes")


class FileUploadResponse(BaseModel):
    """Response model for file upload data"""

    file_id: str = Field(..., description="File ID (stored as _id)", alias="_id")
    project_id: str | None = Field(
        default=None, description="Set when the file belongs to a project workspace"
    )
    file_url: str = Field(..., description="API endpoint to access the file")
    file_metadata: dict[str, Any] = Field(
        ..., description="File metadata (name, type, size, gcs_path)"
    )
    ocr_result: str = Field(..., description="OCR extracted text")
    status: str = Field(
        ..., description="Processing status: processing/completed/failed"
    )
    processing_task_id: str | None = Field(
        default=None, description="Async document processing task id"
    )
    processing_status: str | None = Field(
        default=None, description="Async document processing status"
    )
    processing_error: str | None = Field(
        default=None, description="Safe processing failure message"
    )
    processed_chunk_count: int | None = Field(
        default=None, description="Number of chunks ingested by document processing"
    )
    processing_skipped: bool | None = Field(
        default=None, description="Whether processing completed without indexing"
    )
    processing_skip_reason: str | None = Field(
        default=None, description="Reason processing skipped indexing"
    )
    used_as_ai_reference: bool = Field(
        default=False,
        description="Project files only: whether this file is included as AI context",
    )
    created_at: datetime
    updated_at: datetime
    model_config = {"populate_by_name": True}

    @model_validator(mode="before")
    @classmethod
    def shape_file_metadata(cls, data: Any) -> Any:
        """Derive readiness from the Milvus index, then drop indexing internals.

        The stored ``status`` is unreliable: rest-api-llm writes ingestion
        results into the same Mongo record and leaves ``status`` at its own
        value. ``milvus_file_index.enabled`` is what actually decides whether
        chat can retrieve the file (see ``chat_service._build_file_context``),
        so it is the readiness signal here too.
        """
        if not isinstance(data, dict):
            return data

        metadata = data.get("file_metadata")
        if not isinstance(metadata, dict):
            return data

        metadata = dict(metadata)  # never mutate the caller's Mongo record
        indexed = bool((metadata.pop("milvus_file_index", None) or {}).get("enabled"))
        metadata.pop("ocr_token_count", None)
        return {
            **data,
            "file_metadata": metadata,
            "status": "completed" if indexed else data.get("status"),
        }


class FileStatusUpdateRequest(BaseModel):
    """Request model for updating file status"""

    status: str = Field(..., description="New status: processing/completed/failed")
    ocr_result: str | None = Field(None, description="Optional OCR result")


# Backward compatibility models (to be removed in future versions)
class MessagesFetchRequest(BaseModel):
    """DEPRECATED: Use GET /messages/{session_id} instead"""

    session_id: str = Field(..., description="Session ID")
    limit: int = Field(100, ge=1, le=500)


class FeedbackFetchRequest(BaseModel):
    """DEPRECATED: Use GET /feedback/{message_id} instead"""

    message_id: str = Field(..., description="Message ID")
