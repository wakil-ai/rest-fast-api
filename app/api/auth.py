from authlib.integrations.starlette_client import OAuth
from fastapi import APIRouter, Depends, HTTPException, Request

from app.core.config import settings
from app.core.logger import logger
from app.models.auth import TelegramAuth
from app.services.auth_service import validate_telegram_data
from app.services.chat_history_service import ChatHistoryService

router = APIRouter(prefix="/auth", tags=["Auth for Login"])

# Services
chat_history_service = ChatHistoryService()


# Google OAuth2 setup
oauth = OAuth()
oauth.register(
    name="google",
    client_id=settings.GOOGLE_CLIENT_ID,
    client_secret=settings.GOOGLE_CLIENT_SECRET,
    server_metadata_url="https://accounts.google.com/.well-known/openid-configuration",
    client_kwargs={"scope": "openid email profile"},
)


@router.get("/google/login")
async def login(request: Request):
    """
    Redirect the user to Google's OAuth 2.0 consent screen.
    """
    if not settings.GOOGLE_CLIENT_ID or not settings.GOOGLE_CLIENT_SECRET:
        raise HTTPException(
            status_code=500, detail="Google OAuth credentials not configured."
        )

    redirect_uri = settings.GOOGLE_REDIRECT_URI or str(request.url_for("auth_callback"))
    logger.info(f"[GoogleAuth] Initiating login. Redirect URI: {redirect_uri}")
    return await oauth.google.authorize_redirect(request, redirect_uri)


@router.get("/google/callback", name="auth_callback")
async def auth_callback(request: Request):
    """
    Callback endpoint where Google redirects after authentication.
    """
    # Debug logging
    logger.info(f"[GoogleAuth] Callback reached. Session cookies: {request.cookies}")
    logger.info(
        f"[GoogleAuth] Session keys at callback: {list(request.session.keys())}"
    )

    try:
        token = await oauth.google.authorize_access_token(request)
        user_info = token.get("userinfo")
        if not user_info:
            raise HTTPException(
                status_code=400, detail="Failed to retrieve user info from Google."
            )

        # Sync user with our database
        user_id = user_info.get("sub")  # Google's unique subject ID
        email = user_info.get("email")
        name = user_info.get("name")
        picture = user_info.get("picture")

        # Use email or sub as internal user_id
        internal_user_id = email or user_id

        user = await chat_history_service.create_user(
            user_id=internal_user_id,
            username=email,
            first_name=user_info.get("given_name"),
            last_name=user_info.get("family_name"),
            picture=picture,
        )

        return {
            "success": True,
            "user": {
                "user_id": user.get("user_id"),
                "email": email,
                "name": name,
                "picture": picture,
            },
            "message": "Authentication successful",
        }

    except Exception as e:
        logger.error(f"[GoogleAuth] Error during callback: {e}")
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
            # Return validated user data as JSON
            return {
                "success": True,
                "user": validated_data,
                "message": "Authentication successful",
            }
        else:
            raise HTTPException(
                status_code=400, detail="Authentication validation failed"
            )

    except ValueError as e:
        raise HTTPException(status_code=400, detail=f"Authentication error: {str(e)}")
