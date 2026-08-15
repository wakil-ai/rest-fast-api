"""Global exception handlers producing the coded error envelope.

Registers three handlers on the FastAPI app:

- `HTTPException` (and every subclass, including `core.exceptions.ChatException`
  and its descendants) — emits `error.code` from the exception when present,
  unwraps the three legacy dict-`detail` codes (FILE_UPLOAD_REQUIRES_PAID_PLAN,
  ACTIVE_SUBSCRIPTION_EXISTS, DAILY_PASS_DOWNGRADE_NOT_ALLOWED /
  ACTIVE_DAILY_PASS_SAME_TIER) into the same envelope, and otherwise derives a
  generic code from the status so every response has a consistent shape
  before every raise site is migrated to a coded exception.
- `RequestValidationError` — 422s get `VALIDATION_ERROR` with
  `params.fields`, replacing the raw `[{loc,msg,type}]` array.
- `Exception` — any unhandled error becomes a safe 500 with no leaked detail.

See `core.error_codes` for the wire shape and code catalog.
"""

from fastapi import FastAPI, Request, status
from fastapi.exceptions import HTTPException, RequestValidationError
from fastapi.responses import JSONResponse

from core.config import settings
from core.error_codes import ERROR_CATALOG, STATUS_FALLBACK_CODE, ErrorCode
from core.logger import logger

# Legacy exceptions raised with a dict `detail={"code": ..., "message": ..., ...}`
# instead of going through core.exceptions.ChatException. Unwrapping these here
# means every response — old or new raise site — ends up in the same envelope.
_LEGACY_DETAIL_CODES = {
    "FILE_UPLOAD_REQUIRES_PAID_PLAN",
    "ACTIVE_SUBSCRIPTION_EXISTS",
    "ACTIVE_DAILY_PASS_SAME_TIER",
    "DAILY_PASS_DOWNGRADE_NOT_ALLOWED",
}


def _error_body(detail: str, code: ErrorCode | str, message: str, params: dict) -> dict:
    return {
        "detail": detail,
        "error": {
            "code": code.value if isinstance(code, ErrorCode) else code,
            "message": message,
            "params": params,
        },
    }


async def _http_exception_handler(request: Request, exc: HTTPException) -> JSONResponse:
    code: ErrorCode | str | None = getattr(exc, "code", None)
    params: dict = getattr(exc, "params", None) or {}
    detail = exc.detail

    # Legacy dict-detail exceptions (entitlements.py, payment.py): unwrap into
    # the standard envelope. This is what keeps `detail` a plain string for
    # the mobile clients, which decode it as such.
    if code is None and isinstance(detail, dict) and detail.get("code") in _LEGACY_DETAIL_CODES:
        legacy = dict(detail)
        code = legacy.pop("code")
        detail = legacy.pop("message", str(code))
        params = legacy

    if code is None:
        code = STATUS_FALLBACK_CODE.get(exc.status_code, ErrorCode.INTERNAL_ERROR)

    spec = ERROR_CATALOG.get(code) if isinstance(code, ErrorCode) else None
    message = spec.message if spec else str(code)

    if not isinstance(detail, str):
        # Any other non-string detail we don't recognize — never forward a
        # raw structure to the string-typed `detail` field.
        detail = message

    if exc.status_code >= 500 and not settings.EXPOSE_ERROR_DETAIL:
        logger.error(f"Unhandled 5xx at {request.url.path}: {detail}")
        detail = "Internal server error."
        params = {}

    return JSONResponse(
        status_code=exc.status_code,
        content=_error_body(detail, code, message, params),
        headers=getattr(exc, "headers", None),
    )


async def _validation_exception_handler(
    request: Request, exc: RequestValidationError
) -> JSONResponse:
    fields = [
        {
            "field": ".".join(str(p) for p in err.get("loc", []) if p != "body"),
            "message": err.get("msg", ""),
        }
        for err in exc.errors()
    ]
    detail = "; ".join(f.get("message", "") for f in fields) or "Validation failed."
    spec = ERROR_CATALOG[ErrorCode.VALIDATION_ERROR]

    return JSONResponse(
        status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
        content=_error_body(detail, ErrorCode.VALIDATION_ERROR, spec.message, {"fields": fields}),
    )


async def _unhandled_exception_handler(request: Request, exc: Exception) -> JSONResponse:
    logger.error(f"Unhandled exception at {request.url.path}: {exc!r}")
    spec = ERROR_CATALOG[ErrorCode.INTERNAL_ERROR]
    detail = str(exc) if settings.EXPOSE_ERROR_DETAIL else spec.message

    return JSONResponse(
        status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        content=_error_body(detail, ErrorCode.INTERNAL_ERROR, spec.message, {}),
    )


def register_exception_handlers(app: FastAPI) -> None:
    """Wire the coded-error handlers into `app`. Call once, from `create_app()`."""
    app.add_exception_handler(HTTPException, _http_exception_handler)
    app.add_exception_handler(RequestValidationError, _validation_exception_handler)
    app.add_exception_handler(Exception, _unhandled_exception_handler)
