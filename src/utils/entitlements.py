"""Entitlement guards for paid-plan features (e.g. file upload).

Backend is the source of truth: these checks cannot be bypassed from the client.
"""

from fastapi import HTTPException, status

from core.dependencies import get_rate_limit_service
from core.logger import logger

# Machine-readable contract returned to clients when an upload is denied.
# Raised here with a dict `detail` for historical reasons; the global
# handler in core.error_handlers unwraps this into the standard envelope
# before it reaches the client — {"detail": "<message>", "error": {"code":
# "FILE_UPLOAD_REQUIRES_PAID_PLAN", "message": ..., "params": {"upgrade_required": true}}}.
# See docs/reference-error-codes.md.
FILE_UPLOAD_REQUIRES_PAID_PLAN = "FILE_UPLOAD_REQUIRES_PAID_PLAN"

_UPLOAD_DENIED_DETAIL = {
    "code": FILE_UPLOAD_REQUIRES_PAID_PLAN,
    "message": "File upload is available for paid plans only.",
    "upgrade_required": True,
}


async def ensure_can_upload_files(user_id: str, endpoint: str) -> None:
    """Raise ``402 Payment Required`` if ``user_id`` is not entitled to upload.

    Call this at the API layer *before* any file is read/processed.

    Args:
        user_id: The user attempting the upload.
        endpoint: Human-readable endpoint label, used for denial telemetry.
    """
    rate_limit_service = get_rate_limit_service()
    allowed = await rate_limit_service.can_upload_files(user_id)
    if allowed:
        return

    # Telemetry: log denied attempts without sensitive payloads.
    # (Timestamp is added by the logger formatter.)
    logger.warning(
        f"[Entitlement] Upload denied user_id={user_id} endpoint={endpoint} "
        f"reason={FILE_UPLOAD_REQUIRES_PAID_PLAN}"
    )

    raise HTTPException(
        status_code=status.HTTP_402_PAYMENT_REQUIRED,
        detail=_UPLOAD_DENIED_DETAIL,
    )
