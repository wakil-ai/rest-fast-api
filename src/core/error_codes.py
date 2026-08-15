"""Machine-readable error code catalog.

Every coded failure the API can raise gets a stable `ErrorCode` here. Wire
shape (see `core.error_handlers`):

    {
      "detail": "Insufficient credits. You have 0/30 credits remaining.",
      "error": {
        "code": "CREDITS_EXHAUSTED",
        "message": "Daily credit limit reached.",
        "params": {"remaining": 0, "limit": 30, "required": 1}
      }
    }

`detail` is always a plain string — both mobile clients decode it as such
and must not be broken. `error` is an additive sibling carrying the code
frontends should branch on instead of parsing `detail` text. `error.message`
is an English, developer-facing fallback; product surfaces should localize
by `error.code` instead of displaying it.

Codes are permanent once shipped — a client may already be switching on one.
Add new codes; do not rename or remove existing ones.
"""

from dataclasses import dataclass, field
from enum import Enum

from fastapi import status


class ErrorCode(str, Enum):
    # Generic (used by the normalizing handler for uncoded exceptions)
    BAD_REQUEST = "BAD_REQUEST"
    UNAUTHORIZED = "UNAUTHORIZED"
    FORBIDDEN = "FORBIDDEN"
    NOT_FOUND = "NOT_FOUND"
    CONFLICT = "CONFLICT"
    GONE = "GONE"
    PAYLOAD_TOO_LARGE = "PAYLOAD_TOO_LARGE"
    UNSUPPORTED_MEDIA_TYPE = "UNSUPPORTED_MEDIA_TYPE"
    VALIDATION_ERROR = "VALIDATION_ERROR"
    RATE_LIMITED = "RATE_LIMITED"
    INTERNAL_ERROR = "INTERNAL_ERROR"

    # Chat / credits / quota
    CREDITS_EXHAUSTED = "CREDITS_EXHAUSTED"
    QUERY_TOO_LONG = "QUERY_TOO_LONG"
    FILE_CONTENT_TOO_LONG = "FILE_CONTENT_TOO_LONG"
    ASSISTANT_CONFIG_INVALID = "ASSISTANT_CONFIG_INVALID"
    CHAT_GENERATION_FAILED = "CHAT_GENERATION_FAILED"
    FLOW_EXECUTION_FAILED = "FLOW_EXECUTION_FAILED"
    STREAMING_FAILED = "STREAMING_FAILED"
    SESSION_PROJECT_MISMATCH = "SESSION_PROJECT_MISMATCH"

    # History / ownership
    USER_NOT_FOUND = "USER_NOT_FOUND"
    SESSION_NOT_FOUND = "SESSION_NOT_FOUND"
    MESSAGE_NOT_FOUND = "MESSAGE_NOT_FOUND"
    FILE_NOT_FOUND = "FILE_NOT_FOUND"
    SHARE_NOT_FOUND = "SHARE_NOT_FOUND"
    USER_BLOCKED = "USER_BLOCKED"
    USER_ALREADY_EXISTS = "USER_ALREADY_EXISTS"
    ACCOUNT_DELETED = "ACCOUNT_DELETED"

    # Uploads / entitlements
    FILE_UPLOAD_REQUIRES_PAID_PLAN = "FILE_UPLOAD_REQUIRES_PAID_PLAN"
    FILE_TOO_LARGE = "FILE_TOO_LARGE"
    FILE_TYPE_UNSUPPORTED = "FILE_TYPE_UNSUPPORTED"

    # Project collaboration / invites
    PROJECT_NOT_FOUND = "PROJECT_NOT_FOUND"
    PROJECT_ACCESS_DENIED = "PROJECT_ACCESS_DENIED"
    PROJECT_OWNER_REQUIRED = "PROJECT_OWNER_REQUIRED"
    PROJECT_MEMBER_NOT_FOUND = "PROJECT_MEMBER_NOT_FOUND"
    PROJECT_CANNOT_REMOVE_OWNER = "PROJECT_CANNOT_REMOVE_OWNER"
    PROJECT_CLOSED = "PROJECT_CLOSED"
    PROJECT_INSTRUCTIONS_EXIST = "PROJECT_INSTRUCTIONS_EXIST"
    INVITE_NOT_FOUND = "INVITE_NOT_FOUND"
    INVITE_EXPIRED = "INVITE_EXPIRED"
    INVITE_REVOKED = "INVITE_REVOKED"
    INVITE_ALREADY_USED = "INVITE_ALREADY_USED"
    INVITE_NOT_PENDING = "INVITE_NOT_PENDING"
    INVITE_NOT_MANAGEABLE = "INVITE_NOT_MANAGEABLE"
    INVITE_OWNER_CANNOT_ACCEPT = "INVITE_OWNER_CANNOT_ACCEPT"
    INVITE_CROSS_TENANT = "INVITE_CROSS_TENANT"

    # Organizations (feat/enterprise)
    ORG_NOT_FOUND = "ORG_NOT_FOUND"
    ORG_NOT_MEMBER = "ORG_NOT_MEMBER"
    ORG_ADMIN_REQUIRED = "ORG_ADMIN_REQUIRED"
    ORG_SEAT_LIMIT_REACHED = "ORG_SEAT_LIMIT_REACHED"
    ORG_NOT_ACCEPTING_MEMBERS = "ORG_NOT_ACCEPTING_MEMBERS"
    ORG_NAME_REQUIRED = "ORG_NAME_REQUIRED"

    # Auth / OTP
    INVALID_PHONE_FORMAT = "INVALID_PHONE_FORMAT"
    OTP_INVALID_OR_EXPIRED = "OTP_INVALID_OR_EXPIRED"
    OTP_RATE_LIMITED = "OTP_RATE_LIMITED"
    OTP_SEND_FAILED = "OTP_SEND_FAILED"
    OTP_SERVICE_UNAVAILABLE = "OTP_SERVICE_UNAVAILABLE"
    INCORRECT_CREDENTIALS = "INCORRECT_CREDENTIALS"

    # Promo codes
    PROMO_CODE_NOT_FOUND = "PROMO_CODE_NOT_FOUND"
    PROMO_CODE_ALREADY_EXISTS = "PROMO_CODE_ALREADY_EXISTS"
    PROMO_CODE_ASSIGN_FAILED = "PROMO_CODE_ASSIGN_FAILED"

    # Billing / payments (legacy dict-detail codes — kept verbatim, see
    # models/payment.py:SubscriptionEligibilityError and utils/entitlements.py)
    ACTIVE_SUBSCRIPTION_EXISTS = "ACTIVE_SUBSCRIPTION_EXISTS"
    ACTIVE_DAILY_PASS_SAME_TIER = "ACTIVE_DAILY_PASS_SAME_TIER"
    DAILY_PASS_DOWNGRADE_NOT_ALLOWED = "DAILY_PASS_DOWNGRADE_NOT_ALLOWED"
    PAYMENT_AMOUNT_MISMATCH = "PAYMENT_AMOUNT_MISMATCH"


@dataclass(frozen=True)
class ErrorSpec:
    http_status: int
    # English, developer-facing fallback. Never shown to end users when the
    # frontend recognizes `code` — see module docstring.
    message: str
    # Documents which keys `params` carries for this code. Not enforced at
    # runtime; each raise site supplies its own `params` dict.
    params: tuple[str, ...] = field(default_factory=tuple)


ERROR_CATALOG: dict[ErrorCode, ErrorSpec] = {
    ErrorCode.BAD_REQUEST: ErrorSpec(status.HTTP_400_BAD_REQUEST, "Bad request."),
    ErrorCode.UNAUTHORIZED: ErrorSpec(status.HTTP_401_UNAUTHORIZED, "Unauthorized."),
    ErrorCode.FORBIDDEN: ErrorSpec(status.HTTP_403_FORBIDDEN, "Forbidden."),
    ErrorCode.NOT_FOUND: ErrorSpec(status.HTTP_404_NOT_FOUND, "Not found."),
    ErrorCode.CONFLICT: ErrorSpec(status.HTTP_409_CONFLICT, "Conflict."),
    ErrorCode.GONE: ErrorSpec(status.HTTP_410_GONE, "No longer available."),
    ErrorCode.PAYLOAD_TOO_LARGE: ErrorSpec(status.HTTP_413_REQUEST_ENTITY_TOO_LARGE, "Payload too large."),
    ErrorCode.UNSUPPORTED_MEDIA_TYPE: ErrorSpec(
        status.HTTP_415_UNSUPPORTED_MEDIA_TYPE, "Unsupported media type."
    ),
    ErrorCode.VALIDATION_ERROR: ErrorSpec(
        status.HTTP_422_UNPROCESSABLE_ENTITY, "Validation failed.", ("fields",)
    ),
    ErrorCode.RATE_LIMITED: ErrorSpec(status.HTTP_429_TOO_MANY_REQUESTS, "Too many requests."),
    ErrorCode.INTERNAL_ERROR: ErrorSpec(status.HTTP_500_INTERNAL_SERVER_ERROR, "Internal server error."),

    ErrorCode.CREDITS_EXHAUSTED: ErrorSpec(
        status.HTTP_429_TOO_MANY_REQUESTS,
        "Daily credit limit reached.",
        ("remaining", "limit", "required"),
    ),
    ErrorCode.QUERY_TOO_LONG: ErrorSpec(
        status.HTTP_400_BAD_REQUEST, "Query exceeds the maximum length.", ("length", "max_length")
    ),
    ErrorCode.FILE_CONTENT_TOO_LONG: ErrorSpec(
        status.HTTP_400_BAD_REQUEST,
        "File content exceeds the maximum length.",
        ("file_name", "length", "max_length"),
    ),
    ErrorCode.ASSISTANT_CONFIG_INVALID: ErrorSpec(
        status.HTTP_400_BAD_REQUEST, "Invalid assistant configuration.", ("assistant_name",)
    ),
    ErrorCode.CHAT_GENERATION_FAILED: ErrorSpec(
        status.HTTP_500_INTERNAL_SERVER_ERROR, "Failed to generate answer."
    ),
    ErrorCode.FLOW_EXECUTION_FAILED: ErrorSpec(
        status.HTTP_500_INTERNAL_SERVER_ERROR, "Failed to execute RAG flow."
    ),
    ErrorCode.STREAMING_FAILED: ErrorSpec(
        status.HTTP_500_INTERNAL_SERVER_ERROR, "Streaming response failed."
    ),
    ErrorCode.SESSION_PROJECT_MISMATCH: ErrorSpec(
        status.HTTP_400_BAD_REQUEST, "Session is already linked to a different project."
    ),

    ErrorCode.USER_NOT_FOUND: ErrorSpec(status.HTTP_404_NOT_FOUND, "User not found.", ("user_id",)),
    ErrorCode.SESSION_NOT_FOUND: ErrorSpec(status.HTTP_404_NOT_FOUND, "Session not found.", ("session_id",)),
    ErrorCode.MESSAGE_NOT_FOUND: ErrorSpec(status.HTTP_404_NOT_FOUND, "Message not found.", ("message_id",)),
    ErrorCode.FILE_NOT_FOUND: ErrorSpec(status.HTTP_404_NOT_FOUND, "File not found."),
    ErrorCode.SHARE_NOT_FOUND: ErrorSpec(status.HTTP_404_NOT_FOUND, "Share not found."),
    ErrorCode.USER_BLOCKED: ErrorSpec(status.HTTP_403_FORBIDDEN, "User is blocked.", ("user_id",)),
    ErrorCode.USER_ALREADY_EXISTS: ErrorSpec(
        status.HTTP_400_BAD_REQUEST, "User already exists.", ("external_id",)
    ),
    ErrorCode.ACCOUNT_DELETED: ErrorSpec(status.HTTP_403_FORBIDDEN, "This account has been deleted."),

    ErrorCode.FILE_UPLOAD_REQUIRES_PAID_PLAN: ErrorSpec(
        status.HTTP_402_PAYMENT_REQUIRED,
        "File upload is available for paid plans only.",
        ("upgrade_required",),
    ),
    ErrorCode.FILE_TOO_LARGE: ErrorSpec(status.HTTP_413_REQUEST_ENTITY_TOO_LARGE, "File is too large."),
    ErrorCode.FILE_TYPE_UNSUPPORTED: ErrorSpec(
        status.HTTP_415_UNSUPPORTED_MEDIA_TYPE, "File type is not supported.", ("content_type",)
    ),

    ErrorCode.PROJECT_NOT_FOUND: ErrorSpec(status.HTTP_404_NOT_FOUND, "Project not found."),
    ErrorCode.PROJECT_ACCESS_DENIED: ErrorSpec(status.HTTP_403_FORBIDDEN, "Access to this project is denied."),
    ErrorCode.PROJECT_OWNER_REQUIRED: ErrorSpec(
        status.HTTP_403_FORBIDDEN, "Only the project owner can perform this action."
    ),
    ErrorCode.PROJECT_MEMBER_NOT_FOUND: ErrorSpec(status.HTTP_404_NOT_FOUND, "Member not found."),
    ErrorCode.PROJECT_CANNOT_REMOVE_OWNER: ErrorSpec(
        status.HTTP_400_BAD_REQUEST, "Cannot remove the project owner."
    ),
    ErrorCode.PROJECT_CLOSED: ErrorSpec(status.HTTP_400_BAD_REQUEST, "Project is not accepting new members."),
    ErrorCode.PROJECT_INSTRUCTIONS_EXIST: ErrorSpec(
        status.HTTP_409_CONFLICT, "Project instructions already exist."
    ),
    ErrorCode.INVITE_NOT_FOUND: ErrorSpec(status.HTTP_404_NOT_FOUND, "Invite not found."),
    ErrorCode.INVITE_EXPIRED: ErrorSpec(status.HTTP_410_GONE, "Invite has expired."),
    ErrorCode.INVITE_REVOKED: ErrorSpec(status.HTTP_410_GONE, "Invite has been revoked."),
    ErrorCode.INVITE_ALREADY_USED: ErrorSpec(status.HTTP_410_GONE, "Invite has already been used."),
    ErrorCode.INVITE_NOT_PENDING: ErrorSpec(status.HTTP_400_BAD_REQUEST, "Invite is not pending."),
    ErrorCode.INVITE_NOT_MANAGEABLE: ErrorSpec(
        status.HTTP_403_FORBIDDEN, "Only the owner can manage invite links."
    ),
    ErrorCode.INVITE_OWNER_CANNOT_ACCEPT: ErrorSpec(
        status.HTTP_400_BAD_REQUEST, "Owner cannot accept their own invite."
    ),
    ErrorCode.INVITE_CROSS_TENANT: ErrorSpec(
        status.HTTP_403_FORBIDDEN, "Cannot join from a different product account."
    ),

    ErrorCode.ORG_NOT_FOUND: ErrorSpec(status.HTTP_404_NOT_FOUND, "Organization not found."),
    ErrorCode.ORG_NOT_MEMBER: ErrorSpec(status.HTTP_403_FORBIDDEN, "Not a member of this organization."),
    ErrorCode.ORG_ADMIN_REQUIRED: ErrorSpec(
        status.HTTP_403_FORBIDDEN, "Only an organization admin can perform this action."
    ),
    ErrorCode.ORG_SEAT_LIMIT_REACHED: ErrorSpec(
        status.HTTP_409_CONFLICT, "Organization has reached its seat limit.", ("seat_limit",)
    ),
    ErrorCode.ORG_NOT_ACCEPTING_MEMBERS: ErrorSpec(
        status.HTTP_400_BAD_REQUEST, "Organization is not accepting new members."
    ),
    ErrorCode.ORG_NAME_REQUIRED: ErrorSpec(status.HTTP_400_BAD_REQUEST, "Organization name is required."),

    ErrorCode.INVALID_PHONE_FORMAT: ErrorSpec(
        status.HTTP_400_BAD_REQUEST, "Invalid phone number format.", ("phone",)
    ),
    ErrorCode.OTP_INVALID_OR_EXPIRED: ErrorSpec(status.HTTP_400_BAD_REQUEST, "Invalid or expired OTP code."),
    ErrorCode.OTP_RATE_LIMITED: ErrorSpec(
        status.HTTP_429_TOO_MANY_REQUESTS, "Too many OTP requests.", ("retry_after_seconds",)
    ),
    ErrorCode.OTP_SEND_FAILED: ErrorSpec(status.HTTP_502_BAD_GATEWAY, "Failed to send OTP."),
    ErrorCode.OTP_SERVICE_UNAVAILABLE: ErrorSpec(
        status.HTTP_503_SERVICE_UNAVAILABLE, "OTP service is not configured."
    ),
    ErrorCode.INCORRECT_CREDENTIALS: ErrorSpec(status.HTTP_401_UNAUTHORIZED, "Incorrect email or password."),

    ErrorCode.PROMO_CODE_NOT_FOUND: ErrorSpec(status.HTTP_404_NOT_FOUND, "Promo code not found.", ("code",)),
    ErrorCode.PROMO_CODE_ALREADY_EXISTS: ErrorSpec(
        status.HTTP_400_BAD_REQUEST, "Promo code already exists.", ("code",)
    ),
    ErrorCode.PROMO_CODE_ASSIGN_FAILED: ErrorSpec(
        status.HTTP_400_BAD_REQUEST, "Could not assign promo code."
    ),

    ErrorCode.ACTIVE_SUBSCRIPTION_EXISTS: ErrorSpec(
        status.HTTP_409_CONFLICT,
        "An active subscription already exists for this user.",
        ("active_subscription_end_ms", "active_subscription_tier", "active_subscription_period"),
    ),
    ErrorCode.ACTIVE_DAILY_PASS_SAME_TIER: ErrorSpec(
        status.HTTP_409_CONFLICT, "This daily pass tier is already active.", ("active_daily_pass_end_ms",)
    ),
    ErrorCode.DAILY_PASS_DOWNGRADE_NOT_ALLOWED: ErrorSpec(
        status.HTTP_409_CONFLICT,
        "Downgrading an active daily pass is not allowed.",
        ("active_daily_pass_end_ms",),
    ),
    ErrorCode.PAYMENT_AMOUNT_MISMATCH: ErrorSpec(
        status.HTTP_400_BAD_REQUEST, "Payment amount does not match the subscription price."
    ),
}


# Status codes with no single obvious code — the normalizing handler in
# core.error_handlers falls back to these for an uncoded HTTPException.
STATUS_FALLBACK_CODE: dict[int, ErrorCode] = {
    400: ErrorCode.BAD_REQUEST,
    401: ErrorCode.UNAUTHORIZED,
    403: ErrorCode.FORBIDDEN,
    404: ErrorCode.NOT_FOUND,
    409: ErrorCode.CONFLICT,
    410: ErrorCode.GONE,
    413: ErrorCode.PAYLOAD_TOO_LARGE,
    415: ErrorCode.UNSUPPORTED_MEDIA_TYPE,
    422: ErrorCode.VALIDATION_ERROR,
    429: ErrorCode.RATE_LIMITED,
}
