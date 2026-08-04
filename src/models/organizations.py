from datetime import datetime
from enum import Enum
from typing import Any

from pydantic import BaseModel, Field


class OrganizationStatus(str, Enum):
    active = "active"
    suspended = "suspended"
    archived = "archived"


class OrganizationMembershipRole(str, Enum):
    admin = "admin"
    member = "member"


class OrganizationMemberStatus(str, Enum):
    active = "active"
    removed = "removed"


class OrganizationInviteStatus(str, Enum):
    pending = "pending"
    accepted = "accepted"
    revoked = "revoked"
    expired = "expired"


class OrganizationCreateRequest(BaseModel):
    name: str = Field(..., min_length=1, max_length=120)
    settings: dict[str, Any] = Field(default_factory=dict)


class OrganizationUpdateRequest(BaseModel):
    """Partial edit. Omitted keys are left alone; an explicit null resets the field.

    The route sends ``model_dump(exclude_unset=True)``, which is the only thing that
    tells an omitted ``settings`` apart from one the caller means to reset. The
    picture is absent by design: it is written only by the avatar endpoints, so no
    caller can point an organization at an arbitrary URL.
    """

    name: str | None = Field(None, min_length=1, max_length=120)
    settings: dict[str, Any] | None = None


class OrganizationResponse(BaseModel):
    org_id: str
    name: str
    avatar_url: str | None = Field(
        None,
        description="Permanent public URL of the organization's picture, or null. "
        "Written only by the avatar upload endpoint.",
    )
    created_by: str
    status: OrganizationStatus
    seat_limit: int
    settings: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime
    updated_at: datetime
    role: OrganizationMembershipRole = Field(
        ..., description="The requesting user's role in this organization"
    )
    member_count: int = 0


class OrganizationListResponse(BaseModel):
    organizations: list[OrganizationResponse]


class OrganizationInviteResponse(BaseModel):
    invite_id: str
    org_id: str
    status: OrganizationInviteStatus
    created_by: str
    expires_at: datetime
    created_at: datetime
    join_path: str = Field(
        ...,
        description="Relative path for the frontend invite URL "
        "(e.g. organizations/join/oinv-...)",
    )


class OrganizationInviteListResponse(BaseModel):
    org_id: str
    invites: list[OrganizationInviteResponse]


class OrganizationInvitePreviewResponse(BaseModel):
    invite_id: str
    org_id: str
    org_name: str
    org_avatar_url: str | None = None
    org_status: OrganizationStatus
    status: OrganizationInviteStatus
    expires_at: datetime
    already_member: bool = False
    seats_remaining: int = 0


class OrganizationInviteAcceptResponse(BaseModel):
    org_id: str
    user_id: str
    membership_role: OrganizationMembershipRole
    joined_at: datetime


class OrganizationMemberResponse(BaseModel):
    user_id: str
    role: OrganizationMembershipRole
    status: OrganizationMemberStatus
    joined_at: datetime | None = None
    first_name: str | None = None
    last_name: str | None = None
    username: str | None = None
    picture: str | None = None


class OrganizationMembersListResponse(BaseModel):
    org_id: str
    members: list[OrganizationMemberResponse]
