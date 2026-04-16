import base64
import json
from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse

import httpx
from authlib.integrations.starlette_client import OAuth
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import RedirectResponse

from app.core.config import settings
from app.core.dependencies import get_chat_history_service
from app.core.exceptions import UserAlreadyExistsException
from app.core.logger import logger
from app.models.auth import DTUserCreateRequest, DTUserCreateResponse, TelegramAuth
from app.security.dependencies import verify_dt_api_key
from app.services import validate_telegram_data
from app.utils.user_management import generate_short_id

router = APIRouter(prefix="/auth", tags=["Auth for Login"])

# Services
chat_history_service = get_chat_history_service()


# Google OAuth2 setup with PKCE flow (no session state dependency)
oauth = OAuth()
oauth.register(
    name="google",
    client_id=settings.GOOGLE_CLIENT_ID,
    client_secret=settings.GOOGLE_CLIENT_SECRET,
    server_metadata_url="https://accounts.google.com/.well-known/openid-configuration",
    client_kwargs={"scope": "openid email profile"},
    code_challenge_method="S256",  # Use PKCE (Proof Key for Code Exchange)
)


def _is_allowed_frontend_redirect_uri(frontend_redirect_uri: str | None) -> bool:
    if not frontend_redirect_uri:
        return False

    try:
        parsed = urlparse(frontend_redirect_uri)
    except ValueError:
        return False

    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        return False

    allowed_origins = set(settings.ALLOWED_ORIGINS)
    hostname = parsed.hostname or ""

    if hostname == "wakil.ai" or hostname.endswith(".wakil.ai"):
        return True

    if settings.DEVELOPMENT_MODE:
        allowed_origins.update(
            {
                "http://localhost:3000",
                "http://localhost:8081",
                "http://127.0.0.1:3000",
                "http://127.0.0.1:8081",
            }
        )

    origin = f"{parsed.scheme}://{parsed.netloc}"
    return origin in allowed_origins


def _build_google_user_payload(user_info: dict, internal_user_id: str) -> dict:
    return {
        "id": internal_user_id,
        "email": user_info.get("email"),
        "first_name": user_info.get("given_name")
        or user_info.get("name")
        or "Google User",
        "last_name": user_info.get("family_name"),
        "photo_url": user_info.get("picture"),
        "auth_method": "google",
    }


def _encode_frontend_user_payload(user_payload: dict) -> str:
    raw_payload = json.dumps(user_payload, separators=(",", ":")).encode("utf-8")
    return base64.urlsafe_b64encode(raw_payload).decode("utf-8").rstrip("=")


def _build_frontend_redirect_response(
    frontend_redirect_uri: str,
    *,
    user_payload: dict | None = None,
    error: str | None = None,
) -> RedirectResponse:
    parsed = urlparse(frontend_redirect_uri)
    query = dict(parse_qsl(parsed.query, keep_blank_values=True))

    if user_payload is not None:
        query["user"] = _encode_frontend_user_payload(user_payload)
    if error:
        query["error"] = error

    redirect_target = urlunparse(parsed._replace(query=urlencode(query)))
    return RedirectResponse(url=redirect_target, status_code=302)


@router.get("/google/login")
async def login(request: Request):
    """
    Redirect the user to Google's OAuth 2.0 consent screen.
    """
    if not settings.GOOGLE_CLIENT_ID or not settings.GOOGLE_CLIENT_SECRET:
        raise HTTPException(
            status_code=500, detail="Google OAuth credentials not configured."
        )

    frontend_redirect_uri = request.query_params.get("frontend_redirect_uri")
    if _is_allowed_frontend_redirect_uri(frontend_redirect_uri):
        request.session["google_frontend_redirect_uri"] = frontend_redirect_uri
    else:
        request.session.pop("google_frontend_redirect_uri", None)

    redirect_uri = settings.GOOGLE_REDIRECT_URI or str(request.url_for("auth_callback"))
    logger.info(f"[GoogleAuth] Frontend redirect URI: {frontend_redirect_uri}")
    logger.info(f"[GoogleAuth] Session before redirect: {dict(request.session)}")

    # Build Google OAuth URL manually to ensure redirect_uri matches exactly
    params = {
        "client_id": settings.GOOGLE_CLIENT_ID,
        "redirect_uri": redirect_uri,
        "response_type": "code",
        "scope": "openid email profile",
        "access_type": "offline",
    }
    google_oauth_url = (
        f"https://accounts.google.com/o/oauth2/v2/auth?{urlencode(params)}"
    )

    logger.info("[GoogleAuth] Redirecting to Google OAuth")
    return RedirectResponse(url=google_oauth_url)


@router.get("/google/callback", name="auth_callback")
async def auth_callback(request: Request):
    """
    Callback endpoint where Google redirects after authentication.
    Manually handles token exchange to bypass session-based state validation.
    """
    logger.info("[GoogleAuth] Callback reached.")
    logger.info(f"[GoogleAuth] Query params: {dict(request.query_params)}")

    frontend_redirect_uri = request.session.pop("google_frontend_redirect_uri", None)

    try:
        # Get authorization code from query params
        auth_code = request.query_params.get("code")
        if not auth_code:
            raise HTTPException(
                status_code=400, detail="Missing authorization code from Google."
            )

        # Manually exchange auth code for tokens (bypass session-based state validation)
        redirect_uri = settings.GOOGLE_REDIRECT_URI or str(
            request.url_for("auth_callback")
        )

        async with httpx.AsyncClient() as client:
            token_response = await client.post(
                "https://oauth2.googleapis.com/token",
                data={
                    "client_id": settings.GOOGLE_CLIENT_ID,
                    "client_secret": settings.GOOGLE_CLIENT_SECRET,
                    "code": auth_code,
                    "grant_type": "authorization_code",
                    "redirect_uri": redirect_uri,
                },
                timeout=20.0,
            )

            logger.info(
                f"[GoogleAuth] Token response status: {token_response.status_code}"
            )
            response_text = token_response.text
            logger.info(f"[GoogleAuth] Token response body: {response_text}")

            if token_response.status_code != 200:
                try:
                    error_json = token_response.json()
                    logger.error(f"[GoogleAuth] Token exchange error: {error_json}")
                    error_msg = (
                        error_json.get("error_description")
                        or error_json.get("error")
                        or response_text
                    )
                    raise Exception(f"Token exchange failed: {error_msg}")
                except Exception:
                    logger.error(f"[GoogleAuth] Token exchange error: {response_text}")
                    raise Exception(f"Token exchange failed: {response_text}")

            token_data = token_response.json()
            logger.info(f"[GoogleAuth] Token data keys: {list(token_data.keys())}")

        access_token = token_data.get("access_token")
        if not access_token:
            logger.error(f"[GoogleAuth] No access token in response: {token_data}")
            raise HTTPException(
                status_code=400, detail="Failed to obtain access token from Google."
            )

        logger.info("[GoogleAuth] Successfully obtained access token")

        # Fetch user info using the access token
        async with httpx.AsyncClient() as client:
            userinfo_response = await client.get(
                "https://openidconnect.googleapis.com/v1/userinfo",
                headers={"Authorization": f"Bearer {access_token}"},
                timeout=10.0,
            )
            userinfo_response.raise_for_status()
            user_info = userinfo_response.json()

        if not user_info:
            raise HTTPException(
                status_code=400, detail="Failed to retrieve user info from Google."
            )

        logger.info(
            f"[GoogleAuth] Successfully retrieved user info: {user_info.get('email')}"
        )

        # Sync user with our database
        user_id = user_info.get("sub")  # Google's unique subject ID
        email = user_info.get("email")
        name = user_info.get("name")
        picture = user_info.get("picture")

        # Use email or sub as internal user_id
        internal_user_id = user_id  # put user_id as internal_user_id to avoid issues with email changes.
        frontend_user = _build_google_user_payload(user_info, internal_user_id)

        existing = await chat_history_service.get_user(internal_user_id)
        if existing and existing.get("is_blocked"):
            raise HTTPException(
                status_code=403,
                detail="User is blocked. Please contact support to unblock your account.",
            )

        user = await chat_history_service.create_user(
            user_id=internal_user_id,
            username=email,
            first_name=user_info.get("given_name"),
            last_name=user_info.get("family_name"),
            picture=picture,
            web_client=settings.WAKILAI_WEB_CLIENT_NAME,
        )

        if _is_allowed_frontend_redirect_uri(frontend_redirect_uri):
            return _build_frontend_redirect_response(
                frontend_redirect_uri, user_payload=frontend_user
            )

        return {
            "success": True,
            "user": {
                **frontend_user,
                "user_id": user.get("user_id"),
                "name": name,
                "picture": picture,
            },
            "message": "Authentication successful",
        }

    except HTTPException as exc:
        if _is_allowed_frontend_redirect_uri(frontend_redirect_uri):
            return _build_frontend_redirect_response(
                frontend_redirect_uri, error=str(exc.detail)
            )
        raise
    except Exception as e:
        logger.error(f"[GoogleAuth] Error during callback: {e}", exc_info=True)
        if _is_allowed_frontend_redirect_uri(frontend_redirect_uri):
            return _build_frontend_redirect_response(
                frontend_redirect_uri, error="Authentication failed"
            )
        raise HTTPException(status_code=400, detail=f"Authentication failed: {str(e)}")


# Telegram OAuth2 setup
@router.get("/telegram/login")
async def telegram_login(query_params: TelegramAuth = Depends(TelegramAuth)):
    """
    Telegram authentication endpoint.
    """
    telegram_token = settings.TELEGRAM_BOT_TOKEN

    # Check if hash parameter exists (required for validation)
    if not query_params.model_dump().get("hash"):
        raise HTTPException(
            status_code=400,
            detail="Missing authentication parameters. Please use Telegram login widget.",
        )

    try:
        # Validate Telegram data using the existing service
        validated_data = validate_telegram_data(telegram_token, query_params)

        if validated_data:
            telegram_id = validated_data.get("id")
            if telegram_id is None:
                raise HTTPException(status_code=400, detail="Missing Telegram user id")

            internal_user_id = str(telegram_id)
            existing = await chat_history_service.get_user(internal_user_id)
            if existing and existing.get("is_blocked"):
                raise HTTPException(
                    status_code=403,
                    detail="User is blocked. Please contact support to unblock your account.",
                )

            user = await chat_history_service.create_user(
                user_id=internal_user_id,
                username=validated_data.get("username"),
                first_name=validated_data.get("first_name"),
                last_name=validated_data.get("last_name"),
                picture=validated_data.get("photo_url"),
                web_client=settings.WAKILAI_WEB_CLIENT_NAME,
            )

            # Return validated user data as JSON
            return {
                "success": True,
                "user": {
                    **validated_data,
                    "user_id": user.get("_id") or internal_user_id,
                    "is_blocked": bool(user.get("is_blocked")),
                },
                "message": "Authentication successful",
            }
        else:
            raise HTTPException(
                status_code=400, detail="Authentication validation failed"
            )

    except ValueError as e:
        raise HTTPException(status_code=400, detail=f"Authentication error: {str(e)}")


# DT Server Authentication (DT integration)
@router.post(
    "/dt",
    dependencies=[Depends(verify_dt_api_key)],
    response_model=DTUserCreateResponse,
)
async def auth_createa_dt_user(request: DTUserCreateRequest):
    """
    Create a user for Birdarcha web client (DT integration).

    Only allows requests from the configured DT server IP.
    Expects JSON body with user details
    """
    try:
        # Check if user with external_id already exists
        existing_user = await chat_history_service.get_user_by_external_id(
            request.user_id
        )
        if existing_user:
            return DTUserCreateResponse(
                success=True, user_id=existing_user.get("user_id")
            )

        if request.phone_number:
            # Basic validation for phone number format (can be enhanced with regex)
            if (
                not request.phone_number.startswith("+998")
                or not request.phone_number[1:].isdigit()
            ):
                raise HTTPException(
                    status_code=400,
                    detail="Invalid phone number format. Must start with '+' followed by digits.",
                )
        else:
            raise HTTPException(
                status_code=400, detail="Phone number is required for DT integration."
            )

        # Generate internal user id
        internal_user_id = generate_short_id(prefix="user-", type="uuid7")

        # Create user with internal user id and external_id
        await chat_history_service.create_user(
            user_id=internal_user_id,
            username=request.username,  # email or username
            first_name=request.first_name,
            last_name=request.last_name,
            phone_number=request.phone_number,
            external_id=request.user_id,  # Store DT's user_id as external_id
            web_client=settings.DT_WEB_CLIENT_NAME,
        )

        return DTUserCreateResponse(success=True, user_id=internal_user_id)

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"[DTAuth] Error creating user: {e}")
        raise HTTPException(status_code=500, detail=f"Failed to create user: {str(e)}")
