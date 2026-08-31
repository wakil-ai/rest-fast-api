"""Request/response models for the admin subscription surface.

Every mutation carries a `reason`: these edits create entitlement without a
payment record, so the audit entry has to say why.
"""

from typing import Any, Literal

from pydantic import BaseModel, Field, model_validator

from core.config import settings
from models.payment import SubscriptionPeriod, SubscriptionTier

MutationTarget = Literal["pool", "daily_lot"]
RevokeTarget = Literal["pool", "daily_lot", "all"]


class AdminActionBase(BaseModel):
    reason: str = Field(
        ...,
        min_length=3,
        max_length=500,
        description="Why this manual change was made. Recorded in the audit trail.",
    )


class AdminGrantRequest(AdminActionBase):
    tier: SubscriptionTier
    period: SubscriptionPeriod
    override_eligibility: bool = Field(
        default=False,
        description=(
            "Bypass ACTIVE_SUBSCRIPTION_EXISTS / DAILY_PASS_DOWNGRADE_NOT_ALLOWED. "
            "The bypassed code is recorded on the audit entry."
        ),
    )
    external_reference: str | None = Field(
        default=None,
        max_length=128,
        description="Receipt or ticket id for the payment taken outside the webhook flow.",
    )
    start_mode: Literal["now", "after_current"] = Field(
        default="after_current",
        description=(
            "'after_current' stacks behind an active window (the purchase path's own "
            "behaviour); 'now' closes the current window first and starts fresh."
        ),
    )
    credits_override: int | None = Field(
        default=None,
        ge=0,
        le=settings.ADMIN_MAX_CREDIT_ADJUSTMENT,
        description="Replaces the catalog's total_credits for this grant.",
    )


class AdminExtendRequest(AdminActionBase):
    target: MutationTarget = "pool"
    daily_lot_id: str | None = None
    extend_days: int | None = Field(
        default=None, ge=1, le=settings.ADMIN_MAX_GRANT_DAYS
    )
    new_end_ms: int | None = Field(default=None, gt=0)
    preserve_remaining: bool = Field(
        default=True,
        description=(
            "Hold the effective balance steady. Widening the window otherwise pulls "
            "in creditusage rows from days outside the original subscription."
        ),
    )

    @model_validator(mode="after")
    def _exactly_one_bound(self) -> "AdminExtendRequest":
        if (self.extend_days is None) == (self.new_end_ms is None):
            raise ValueError("Provide exactly one of extend_days or new_end_ms")
        if self.target == "daily_lot" and not self.daily_lot_id:
            raise ValueError("daily_lot_id is required when target is daily_lot")
        return self


class AdminAdjustCreditsRequest(AdminActionBase):
    mode: Literal["set", "delta"] = "set"
    credits: int = Field(
        ...,
        ge=-settings.ADMIN_MAX_CREDIT_ADJUSTMENT,
        le=settings.ADMIN_MAX_CREDIT_ADJUSTMENT,
        description="Absolute balance when mode='set'; signed change when mode='delta'.",
    )
    target: MutationTarget = "pool"
    daily_lot_id: str | None = None

    @model_validator(mode="after")
    def _check_target(self) -> "AdminAdjustCreditsRequest":
        if self.mode == "set" and self.credits < 0:
            raise ValueError("credits must be >= 0 when mode is 'set'")
        if self.target == "daily_lot" and not self.daily_lot_id:
            raise ValueError("daily_lot_id is required when target is daily_lot")
        return self


class AdminRevokeRequest(AdminActionBase):
    target: RevokeTarget = "pool"
    daily_lot_id: str | None = None
    zero_credits: bool = Field(
        default=True,
        description="Also zero the balance so the diagnostic shows no phantom credits.",
    )

    @model_validator(mode="after")
    def _check_target(self) -> "AdminRevokeRequest":
        if self.target == "daily_lot" and not self.daily_lot_id:
            raise ValueError("daily_lot_id is required when target is daily_lot")
        return self


class AdminSubscriptionWarning(BaseModel):
    code: str
    field: str | None = None
    message: str


class AdminSubscriptionSnapshot(BaseModel):
    raw_subscription: dict[str, Any] | None = None
    computed: dict[str, Any] | None = None
    daily_lot_count: int = 0


class AdminSubscriptionMutationResponse(BaseModel):
    success: bool = True
    action: str
    user_id: str
    audit_id: str | None = None
    request_id: str
    operator: str
    applied_at_ms: int
    before: AdminSubscriptionSnapshot
    after: AdminSubscriptionSnapshot
    delta: dict[str, Any] = Field(default_factory=dict)
    warnings: list[AdminSubscriptionWarning] = Field(default_factory=list)


class AdminSubscriptionUserSummary(BaseModel):
    exists: bool
    id_field: str | None = None
    username: str | None = None
    first_name: str | None = None
    last_name: str | None = None
    phone_number: str | None = None
    web_client: str | None = None
    is_blocked: bool | None = None
    created_at: Any | None = None


class AdminSubscriptionRawBlock(BaseModel):
    subscription: dict[str, Any] | None = None
    subscription_bson_types: dict[str, str] = Field(default_factory=dict)
    legacy_user_subscription: dict[str, Any] | None = None
    daily_lots: list[dict[str, Any]] = Field(default_factory=list)


class AdminSubscriptionReconciliation(BaseModel):
    """Why the stored numbers and the effective numbers disagree, if they do."""

    stored_total_credits: int = 0
    stored_credits_remaining: int = 0
    creditusage_used: int = 0
    legacy_used: int = 0
    effective_used: int = 0
    computed_remaining: int = 0
    expected_total_credits_for_stored_remaining: int = 0
    drift: int = 0


class AdminSubscriptionDiagnosticResponse(BaseModel):
    user_id: str
    user: AdminSubscriptionUserSummary
    raw: AdminSubscriptionRawBlock
    computed: dict[str, Any] | None = None
    credit_usage: dict[str, Any] = Field(default_factory=dict)
    reconciliation: AdminSubscriptionReconciliation
    warnings: list[AdminSubscriptionWarning] = Field(default_factory=list)


class AdminAuditListResponse(BaseModel):
    items: list[dict[str, Any]]
    total: int
    limit: int
    offset: int
