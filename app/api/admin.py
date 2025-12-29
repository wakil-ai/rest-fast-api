# app/api/admin.py

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from app.services.rate_limit_service import RateLimitService
from app.core.logger import logger

router = APIRouter(prefix="/admin", tags=["Admin"])

# Initialize service
rate_limit_service = RateLimitService()


class RateLimitResponse(BaseModel):
    user_id: str
    remaining_requests: int
    daily_limit: int


class ResetLimitRequest(BaseModel):
    user_id: str


@router.get("/rate-limit/{user_id}", response_model=RateLimitResponse, summary="Get user's remaining requests")
async def get_user_rate_limit(user_id: str):
    """
    Get the remaining requests for a user for today.
    
    Parameters:
    - user_id: User ID to check
    
    Returns:
    - remaining_requests: Number of requests remaining today
    - daily_limit: Total daily limit
    """
    try:
        remaining = rate_limit_service.get_remaining_requests(user_id)
        return RateLimitResponse(
            user_id=user_id,
            remaining_requests=remaining,
            daily_limit=RateLimitService.DAILY_LIMIT
        )
    except Exception as e:
        logger.error(f"[AdminAPI] Error getting rate limit for user {user_id}: {str(e)}")
        raise HTTPException(
            status_code=500,
            detail="Failed to retrieve rate limit information"
        )


@router.post("/rate-limit/reset", summary="Reset rate limit for a user")
async def reset_user_rate_limit(request: ResetLimitRequest):
    """
    Reset the rate limit for a specific user (admin function).
    This removes today's rate limit record, allowing the user to make requests again.
    
    Parameters:
    - user_id: User ID to reset
    
    Returns:
    - success: Whether the reset was successful
    - message: Status message
    """
    try:
        success = rate_limit_service.reset_user_limit(request.user_id)
        if success:
            return {
                "success": True,
                "message": f"Rate limit reset successfully for user {request.user_id}"
            }
        else:
            return {
                "success": False,
                "message": f"No rate limit record found for user {request.user_id} today"
            }
    except Exception as e:
        logger.error(f"[AdminAPI] Error resetting rate limit for user {request.user_id}: {str(e)}")
        raise HTTPException(
            status_code=500,
            detail="Failed to reset rate limit"
        )
