from datetime import datetime
from enum import Enum
from typing import Literal

from pydantic import BaseModel, Field


class ProjectInviteStatus(str, Enum):
    pending = "pending"
    accepted = "accepted"
    revoked = "revoked"
    expired = "expired"


class ProjectMembershipRole(str, Enum):
    owner = "owner"
    member = "member"


class ProjectMemberResponse(BaseModel):
    user_id: str
    role: ProjectMembershipRole
    joined_at: datetime | None = None
    first_name: str | None = None
    last_name: str | None = None
    username: str | None = None
    picture: str | None = None


class ProjectMembersListResponse(BaseModel):
    project_id: str
    members: list[ProjectMemberResponse]


class ProjectInviteCreateRequest(BaseModel):
    """Deprecated: create invite uses ``user_id`` query param on POST, not this body."""

    user_id: str = Field(..., description="Project owner user ID")


class ProjectInviteResponse(BaseModel):
    invite_id: str
    project_id: str
    status: ProjectInviteStatus
    expires_at: datetime
    created_at: datetime
    join_path: str = Field(
        ...,
        description="Relative path for the frontend invite URL (e.g. projects/join/pinv-...)",
    )


class ProjectInvitePreviewResponse(BaseModel):
    invite_id: str
    project_id: str
    project_title: str
    project_status: str
    owner_id: str
    owner_display_name: str | None = None
    status: ProjectInviteStatus
    expires_at: datetime
    already_member: bool = False
    is_owner: bool = False


class ProjectInviteAcceptRequest(BaseModel):
    user_id: str = Field(..., description="User accepting the invite")


class ProjectInviteAcceptResponse(BaseModel):
    project_id: str
    user_id: str
    membership_role: Literal["member"] = "member"
    joined_at: datetime
