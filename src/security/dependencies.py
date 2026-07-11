import base64
import secrets

from fastapi import Depends, HTTPException, Request, Security, status
from fastapi.security import APIKeyHeader, HTTPBasic, HTTPBasicCredentials

from core.config import settings
from core.dependencies import get_chat_history_service

# HTTP Basic (docs)
security = HTTPBasic()
chat_history_service = get_chat_history_service()

# Key Headers

# Regular API Keys
api_key_header = APIKeyHeader(
    name=settings.API_KEY_NAME.lower(),
    auto_error=False,
    description="HBAI API Key",
)

# Super Admin API Key for sensitive endpoints (logs, admin actions)
super_admin_key_header = APIKeyHeader(
    name=settings.SUPER_ADMIN_KEY_NAME.lower(),
    auto_error=False,
    description="Super Admin API Key — required for logs and sensitive admin endpoints",
)

# DT Team API Key
dt_team_key_header = APIKeyHeader(
    name=settings.DT_API_KEY_NAME.lower(),
    auto_error=False,
    description="DT Team API Key — required for DT team backend access",
)


# Verification functions


# Docs Basic Auth
def get_current_username(credentials: HTTPBasicCredentials = Depends(security)):
    correct_username = secrets.compare_digest(credentials.username, settings.DOCS_USER)
    correct_password = secrets.compare_digest(
        credentials.password, settings.DOCS_PASSWORD
    )
    if not (correct_username and correct_password):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Incorrect email or password",
            headers={"WWW-Authenticate": "Basic"},
        )
    return credentials.username


def verify_api_key(api_key: str = Security(api_key_header)):
    """Verify API key authentication."""
    if api_key is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="API Key required",
            headers={"WWW-Authenticate": "ApiKey"},
        )
    if not secrets.compare_digest(api_key, settings.API_KEY):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid API Key",
            headers={"WWW-Authenticate": "ApiKey"},
        )
    return True


def verify_super_admin_key(api_key: str = Security(super_admin_key_header)):
    """Verify the super-admin API key (separate from the regular API key)."""
    if api_key is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Super Admin API Key required",
        )
    if not secrets.compare_digest(api_key, settings.SUPER_ADMIN_API_KEY):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Invalid Super Admin API Key",
        )
    return True


def verify_dt_api_key(
    api_key: str = Security(dt_team_key_header), request: Request = None
) -> bool:
    """Verify the DT team API key.

    Only API key validation is required. IP validation is handled by Cloudflare.
    """
    # Verify API key
    if api_key is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="DT Team API Key required",
        )
    if settings.DT_API_KEY is None:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="DT Team API Key not configured",
        )
    if not secrets.compare_digest(api_key, settings.DT_API_KEY):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Invalid DT Team API Key",
        )

    return True


def verify_api_key_or_dt_key(request: Request) -> bool:
    """Verify either default API key OR DT team key.

    IP validation is handled by Cloudflare, only API key checks are performed.

    Logic:
    - If request has default API_KEY header: Allow if valid
    - If request has DT_TEAM_API_KEY header: Allow if valid
    - If neither: Reject with 401
    """
    default_api_key = request.headers.get(settings.API_KEY_NAME.lower())
    dt_team_api_key = request.headers.get(settings.DT_API_KEY_NAME.lower())

    # Check if default API key is provided
    if default_api_key:
        if secrets.compare_digest(default_api_key, settings.API_KEY):
            return True
        else:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Invalid API Key",
            )

    # Check if DT team API key is provided
    if dt_team_api_key:
        if settings.DT_API_KEY is None:
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="DT Team API Key not configured",
            )
        if not secrets.compare_digest(dt_team_api_key, settings.DT_API_KEY):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Invalid DT Team API Key",
            )
        return True

    # Neither key provided
    raise HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="API Key required (use either admin or x-dt-team-api-key header)",
    )


async def assert_not_archived(user_id: str | None) -> None:
    """Raise 403 if ``user_id`` belongs to an archived (deleted) account.

    Explicit form for handlers that receive ``user_id`` in a shape the router-level
    guard can't see (e.g. multipart form uploads). No-op when ``user_id`` is falsy.
    """
    if not user_id or not user_id.strip():
        return
    if await chat_history_service.is_user_archived(str(user_id)):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="This account has been deleted and cannot be used.",
        )


# Route path suffixes exempt from the archive guard. The delete-account endpoint must
# stay reachable by an already-archived user so the request remains idempotent.
_ARCHIVE_GUARD_EXEMPT_SUFFIXES = ("/delete-account",)

# Request fields that name the *acting* user (the caller on whose behalf the request is
# made). ``owner_id`` is the actor on project routes (e.g. GET /projects/{id}?owner_id=).
# Fields that name a *different* user (e.g. ``member_user_id`` being removed) are
# intentionally excluded — we block archived callers, not archived referents.
_ARCHIVE_GUARD_ACTOR_FIELDS = ("user_id", "owner_id")


async def verify_not_archived(request: Request) -> bool:
    """Router-level guard: block requests made on behalf of an archived account.

    Resolves the acting user id from the path, query, then a JSON body (Starlette
    caches the body, so re-reading it here does not consume it for the handler).
    Multipart/form routes carry the id in a shape this can't see — those call
    ``assert_not_archived`` explicitly instead. Routes without any actor id (e.g.
    session-id/message-id-only routes) are covered by read-filtering (Step 5).
    """
    route = request.scope.get("route")
    route_path = getattr(route, "path", "") or ""
    if any(route_path.endswith(suffix) for suffix in _ARCHIVE_GUARD_EXEMPT_SUFFIXES):
        return True

    actor_id: str | None = None
    for field in _ARCHIVE_GUARD_ACTOR_FIELDS:
        actor_id = request.path_params.get(field) or request.query_params.get(field)
        if actor_id:
            break

    if not actor_id and request.method in {"POST", "PUT", "PATCH"}:
        content_type = request.headers.get("content-type", "")
        if content_type.startswith("application/json"):
            try:
                body = await request.json()
            except Exception:
                body = None
            if isinstance(body, dict):
                for field in _ARCHIVE_GUARD_ACTOR_FIELDS:
                    if body.get(field):
                        actor_id = body.get(field)
                        break

    await assert_not_archived(actor_id)
    return True


async def verify_dt_user_web_client(user_id: str) -> bool:
    """Verify that a user belongs to DT client (birdarcha).

    Args:
        user_id: The user ID to check

    Returns:
        True if user is a DT user

    Raises:
        HTTPException: If user not found or doesn't belong to DT client
    """
    # Check if user exists and belongs to DT client
    user = await chat_history_service.get_user(user_id)
    if not user:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="User not found",
        )

    user_web_client = user.get("web_client", settings.WAKILAI_WEB_CLIENT_NAME)
    if user_web_client != settings.DT_WEB_CLIENT_NAME:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Forbidden: User does not belong to DT client (birdarcha)",
        )

    return True


# Dependency Injection for Orchestration and Services
def verify_payme_authorization(authorization: str | None) -> bool:
    """Verify Payme authorization header"""
    if not authorization:
        return False

    try:
        # Extract credentials from "Basic base64string"
        auth_type, credentials = authorization.split(" ", 1)
        if auth_type.lower() != "basic":
            return False

        # Decode base64 credentials
        decoded = base64.b64decode(credentials).decode("utf-8")
        # Should be in format "Paycom:merchant_key"
        username, password = decoded.split(":", 1)

        if username != "Paycom":
            return False

        # Compare with configured merchant key
        if not secrets.compare_digest(password, settings.PAYME_MERCHANT_KEY):
            return False

        return True

    except Exception:
        return False


def verify_uzum_authorization(authorization: str | None) -> bool:
    """Verify Uzum Merchant API Basic Auth header.

    Uzum sends `Authorization: Basic base64(username:password)` on every webhook.
    Username/password are the values WE configure on our side and share with Uzum.
    """
    if not authorization:
        return False

    if not settings.UZUM_USERNAME or not settings.UZUM_PASSWORD:
        return False

    try:
        auth_type, credentials = authorization.split(" ", 1)
        if auth_type.lower() != "basic":
            return False

        decoded = base64.b64decode(credentials).decode("utf-8")
        username, password = decoded.split(":", 1)

        username_ok = secrets.compare_digest(username, settings.UZUM_USERNAME)
        password_ok = secrets.compare_digest(password, settings.UZUM_PASSWORD)
        return username_ok and password_ok
    except Exception:
        return False
