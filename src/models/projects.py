from datetime import datetime
from enum import Enum
from typing import Any, Literal

from pydantic import BaseModel, Field


class ProjectStatus(str, Enum):
    active = "active"
    archived = "archived"
    closed = "closed"


class ProjectSettings(BaseModel):
    """UI and agent preferences scoped to a project."""

    instructions: str | None = None
    name: str | None = None
    icon: str | None = None
    accent_color: str | None = None
    project_image: str | None = None


class ProjectStats(BaseModel):
    docs: int = 0
    chats: int = 0
    reminders: int = 0


class ProjectCreateRequest(BaseModel):
    owner_id: str = Field(..., description="User ID owning the project")
    title: str = Field(default="New project", max_length=500)
    metadata: dict[str, Any] = Field(default_factory=dict)
    settings: ProjectSettings | None = None


class ProjectUpdateRequest(BaseModel):
    """Patch project title, lifecycle status, case metadata, or settings."""

    owner_id: str = Field(..., description="User ID for ownership check")
    title: str | None = None
    status: ProjectStatus | None = None
    settings: ProjectSettings | None = None


class ProjectResponse(BaseModel):
    project_id: str = Field(..., alias="_id")
    owner_id: str
    title: str
    files: list[str] = Field(default_factory=list)
    status: ProjectStatus
    metadata: dict[str, Any] = Field(default_factory=dict)
    stats: ProjectStats = Field(default_factory=ProjectStats)
    settings: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime
    updated_at: datetime
    membership_role: Literal["owner", "member"] | None = Field(
        default=None,
        description="Caller's role on this project when listing or fetching",
    )

    model_config = {"populate_by_name": True}


class ProjectSessionCreateRequest(BaseModel):
    user_id: str
    title: str | None = Field(default="New Chat")
    tags: list[str] = Field(default_factory=list)


class ProjectFileSearchQuery(BaseModel):
    user_id: str
    q: str = Field(..., min_length=1, description="Search query text")
    top_k: int = Field(default=8, ge=1, le=50)


class ProjectFileReferenceUpdateRequest(BaseModel):
    user_id: str = Field(..., description="User ID for ownership check")
    used_as_ai_reference: bool = Field(
        ..., description="Whether this file should be included as AI context"
    )


class ProjectInstructionsResponse(BaseModel):
    project_id: str
    instructions: str | None = None
    updated_at: datetime


class ProjectInstructionsWriteRequest(BaseModel):
    user_id: str = Field(..., description="User ID for ownership check")
    instructions: str = Field(
        ...,
        max_length=20000,
        description="Project-scoped instructions appended after the main assistant prompt",
    )
