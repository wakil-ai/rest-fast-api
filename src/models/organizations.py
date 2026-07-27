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
    icon: str | None = Field(None, max_length=512, description="Icon URL or emoji")
    settings: dict[str, Any] = Field(default_factory=dict)


class OrganizationResponse(BaseModel):
    org_id: str
    name: str
    icon: str | None = None
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
    org_icon: str | None = None
    org_status: OrganizationStatus
    status: OrganizationInviteStatus
    expires_at: datetime
    already_member: bool = False
    seats_remaining: int = 0


class OrganizationInviteAcceptRequest(BaseModel):
    user_id: str = Field(..., description="User accepting the invite")


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
