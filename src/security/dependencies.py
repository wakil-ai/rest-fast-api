import base64
import json
import re
import secrets
import uuid

from fastapi import Depends, HTTPException, Request, Security, status
from fastapi.security import (
    APIKeyHeader,
    HTTPAuthorizationCredentials,
    HTTPBasic,
    HTTPBasicCredentials,
    HTTPBearer,
)
from jwt.exceptions import ExpiredSignatureError, InvalidTokenError

from core.config import settings
from core.dependencies import get_chat_history_service, get_redis_service

# HTTP Basic (docs)
security = HTTPBasic()
chat_history_service = get_chat_history_service()
redis_service = get_redis_service()

_AUTH_USER_STATUS_CACHE_PREFIX = "auth:user-status:"


def _auth_user_status_cache_key(user_id: str) -> str:
    return f"{_AUTH_USER_STATUS_CACHE_PREFIX}{user_id}"


def invalidate_user_auth_cache(user_id: str) -> bool:
    """Invalidate cached authentication status after a user state change."""
    return redis_service.invalidate_cache(_auth_user_status_cache_key(user_id))


async def get_cached_user_auth_status(user_id: str) -> dict:
    """Return minimal user auth status from Redis, falling back to MongoDB."""
    cache_key = _auth_user_status_cache_key(user_id)
    cached = redis_service.cache_get(cache_key)
    if cached is not None:
        try:
            status_data = json.loads(cached)
            status_fields = ("exists", "is_blocked", "archived")
            if isinstance(status_data, dict) and all(
                type(status_data.get(field)) is bool for field in status_fields
            ):
                return {
                    field: status_data[field] for field in status_fields
                }
        except (TypeError, ValueError):
            pass

    user_status = await chat_history_service.get_user_auth_status(user_id)
    status_data = user_status or {
        "exists": False,
        "is_blocked": False,
        "archived": False,
    }
    redis_service.cache_set(
        cache_key,
        status_data,
        ttl_seconds=settings.AUTH_USER_STATUS_CACHE_TTL_SECONDS,
    )
    return status_data


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

user_bearer = HTTPBearer(
    auto_error=False,
    scheme_name="BearerAuth",
    description="WakilAI JWT access token",
)

# Admin action attribution. The super-admin key authorizes but does not identify,
# so mutating admin routes also carry a self-reported operator label and a
# client-minted idempotency key.
admin_operator_header = APIKeyHeader(
    name=settings.ADMIN_OPERATOR_HEADER_NAME.lower(),
    auto_error=False,
    description="Operator label recorded on the admin audit entry (a claim, not proof)",
)

admin_request_id_header = APIKeyHeader(
    name=settings.ADMIN_REQUEST_ID_HEADER_NAME.lower(),
    auto_error=False,
    description="UUID idempotency key; replaying one returns the original result",
)

# Deliberately narrow: names, emails, and handles, but nothing that would let a
# caller smuggle newlines or control characters into an audit record.
_ADMIN_OPERATOR_PATTERN = re.compile(r"^[A-Za-z0-9._@ -]{2,64}$")


# Verification functions


async def get_current_user_id(
    credentials: HTTPAuthorizationCredentials | None = Security(user_bearer),
) -> str:
    """Validate a JWT bearer token and return its user subject."""
    if credentials is None or credentials.scheme.lower() != "bearer":
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Bearer token required",
            headers={"WWW-Authenticate": "Bearer"},
        )

    from services.jwt_service import JWTService

    try:
        payload = JWTService().verify(credentials.credentials)
    except ExpiredSignatureError as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Access token has expired",
            headers={"WWW-Authenticate": 'Bearer error="expired_token"'},
        ) from exc
    except (InvalidTokenError, ValueError) as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid access token",
            headers={"WWW-Authenticate": 'Bearer error="invalid_token"'},
        ) from exc

    user_status = await get_cached_user_auth_status(payload.sub)
    if not user_status["exists"]:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="User does not exist",
            headers={"WWW-Authenticate": 'Bearer error="invalid_token"'},
        )
    if user_status["is_blocked"]:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="User is blocked",
        )
    if user_status["archived"]:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Account is archived",
        )
    return payload.sub


async def verify_user_or_service_auth(request: Request) -> bool:
    """Authenticate the caller and reject archived acting users.

    Service API keys retain their existing behavior for backend integrations.
    A bearer-authenticated request records its subject on ``request.state`` so
    handlers can bind operations to that user. The archive guard is composed
    here so protected routes only need one dependency.
    """
    authorization = request.headers.get("authorization", "")
    scheme, _, token = authorization.partition(" ")

    if scheme.lower() == "bearer":
        if not token.strip():
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Bearer token required",
                headers={"WWW-Authenticate": "Bearer"},
            )
        user_id = await get_current_user_id(
            HTTPAuthorizationCredentials(scheme=scheme, credentials=token)
        )
        request.state.authenticated_user_id = user_id
        await verify_authenticated_actor(request)
        return True

    # A non-Bearer Authorization header (a proxy adding Basic, the docs page's own
    # Basic credentials) must not shadow a valid service key: fall through instead
    # of rejecting outright.
    verify_api_key_or_dt_key(request)
    await verify_not_archived(request)
    return True


async def verify_authenticated_actor(request: Request) -> bool:
    """Prevent a JWT user from submitting another user's actor ID."""
    authenticated_user_id = getattr(request.state, "authenticated_user_id", None)
    if not authenticated_user_id:
        return True

    actor_id = None
    for field in _ARCHIVE_GUARD_ACTOR_FIELDS:
        actor_id = request.path_params.get(field) or request.query_params.get(field)
        if actor_id:
            break

    if not actor_id and request.method in {"POST", "PUT", "PATCH", "DELETE"}:
        content_type = request.headers.get("content-type", "")
        if content_type.startswith("application/json"):
            try:
                body = await request.json()
            except Exception:
                body = None
            if isinstance(body, dict):
                for field in _ARCHIVE_GUARD_ACTOR_FIELDS:
                    if body.get(field):
                        actor_id = str(body[field])
                        break

    if actor_id and not secrets.compare_digest(
        str(actor_id), str(authenticated_user_id)
    ):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Token subject does not match the requested user",
        )
    return True


def assert_authenticated_user_id(request: Request, user_id: str) -> None:
    """Bind multipart/form user IDs to the authenticated JWT subject."""
    authenticated_user_id = getattr(request.state, "authenticated_user_id", None)
    if authenticated_user_id and not secrets.compare_digest(
        str(user_id), str(authenticated_user_id)
    ):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Token subject does not match the requested user",
        )


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
            detail=f"API Key required (use the {settings.API_KEY_NAME.lower()} header)",
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


def get_admin_operator(operator: str = Security(admin_operator_header)) -> str:
    """The operator label recorded on an admin audit entry.

    Anyone holding the super-admin key can write any name here, so this is a
    *claim*. The audit record stores it next to a fingerprint of the key that was
    actually used, which is what turns the claim into evidence once per-operator
    keys exist.
    """
    candidate = (operator or "").strip()
    if not _ADMIN_OPERATOR_PATTERN.fullmatch(candidate):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={
                "code": "ADMIN_OPERATOR_REQUIRED",
                "message": "An operator label is required for admin subscription changes.",
                "header": settings.ADMIN_OPERATOR_HEADER_NAME.lower(),
            },
        )
    return candidate


def get_admin_request_id(request_id: str = Security(admin_request_id_header)) -> str:
    """Client-minted idempotency key for a mutating admin action."""
    candidate = (request_id or "").strip()
    try:
        uuid.UUID(candidate)
    except ValueError:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={
                "code": "ADMIN_REQUEST_ID_REQUIRED",
                "message": "A UUID idempotency key is required for admin subscription changes.",
                "header": settings.ADMIN_REQUEST_ID_HEADER_NAME.lower(),
            },
        ) from None
    return candidate


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
        detail=(
            f"API Key required (use either {settings.API_KEY_NAME.lower()} "
            f"or {settings.DT_API_KEY_NAME.lower()} header)"
        ),
    )


async def assert_not_archived(user_id: str | None) -> None:
    """Raise 403 if ``user_id`` belongs to an archived (deleted) account.

    Explicit form for handlers that receive ``user_id`` in a shape the router-level
    guard can't see (e.g. multipart form uploads). No-op when ``user_id`` is falsy.
    """
    if not user_id or not user_id.strip():
        return
    user_status = await get_cached_user_auth_status(str(user_id))
    if user_status["archived"]:
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
