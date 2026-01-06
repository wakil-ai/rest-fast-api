from fastapi import APIRouter, HTTPException, Depends
from app.models.auth import TelegramAuth
from app.services.auth_service import validate_telegram_data
from app.core.config import settings

router = APIRouter(prefix="/auth", tags=["Telegram Auth"])


@router.get("/login")
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
