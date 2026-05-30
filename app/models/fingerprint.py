from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field


class FingerprintCollectRequest(BaseModel):
    """Client-submitted browser / device fingerprint event."""

    user_id: str = Field(..., min_length=1, max_length=128)
    visitor_id: str = Field(
        description="Stable device fingerprint hash (e.g. FingerprintJS visitorId)",
    )
    metadata: dict[str, Any] = Field(
        default_factory=dict,
        description="Optional client context (screen, timezone, platform, confidence, etc.)",
    )


class FingerprintCollectResponse(BaseModel):
    success: bool = True
    visitor_id: str
    user_id: str
    access_count: int
    account_count_on_device: int = Field(
        ...,
        description="Distinct user accounts seen on this visitor_id",
    )
    is_blocked: bool = False
    is_multi_account: bool = Field(
        ...,
        description="True when more than one account uses this visitor_id",
    )


class FingerprintUserLink(BaseModel):
    user_id: str
    visitor_id: str | None = None
    first_seen_at: datetime
    last_seen_at: datetime
    access_count: int
    is_blocked: bool = False
    ip_addresses: list[str] = Field(default_factory=list)
    user_agents: list[str] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)


class MultiAccountFingerprint(BaseModel):
    visitor_id: str
    user_ids: list[str]
    account_count: int
    last_seen_at: datetime
    total_access_count: int
    is_blocked: bool = False


class MultiAccountFingerprintsResponse(BaseModel):
    items: list[MultiAccountFingerprint]
    total: int


class UserFingerprintsResponse(BaseModel):
    user_id: str
    fingerprints: list[FingerprintUserLink]


class VisitorFingerprintDetailResponse(BaseModel):
    visitor_id: str
    accounts: list[FingerprintUserLink]
    account_count: int
    is_blocked: bool


class FingerprintBlockRequest(BaseModel):
    visitor_id: str = Field(..., min_length=8, max_length=256)
    reason: str | None = Field(default=None, max_length=500)
    blocked_by: str | None = Field(
        default=None,
        max_length=128,
        description="Admin identifier performing the block",
    )


class FingerprintBlockResponse(BaseModel):
    success: bool = True
    visitor_id: str
    modified_count: int
