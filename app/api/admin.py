from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from app.core.logger import logger
from app.models.promo_code import PromoCodeCreate, UserPromoCode
from app.services.promo_code_service import PromoCodeService
from app.services.rate_limit_service import RateLimitService

router = APIRouter(prefix="/admin", tags=["Admin"])

# Initialize services
rate_limit_service = RateLimitService()
promo_code_service = PromoCodeService()


class RateLimitResponse(BaseModel):
    user_id: str
    remaining_credits: int
    daily_credit_limit: int
    credit_costs: dict  # Shows credit cost for each assistant type


class ResetLimitRequest(BaseModel):
    user_id: str


@router.get(
    "/rate-limit/{user_id}",
    response_model=RateLimitResponse,
    summary="Get user's remaining credits",
)
async def get_user_rate_limit(user_id: str):
    """
    Get rate limit information for a specific user.

    Parameters:
    - user_id: User ID to check

    Returns:
    - remaining_credits: Number of credits remaining today
    - daily_credit_limit: Total daily credit limit (100)
    - credit_costs: Credit cost for each assistant type
    """
    try:
        from app.core.config import settings

        remaining = rate_limit_service.get_remaining_credits(user_id)
        return RateLimitResponse(
            user_id=user_id,
            remaining_credits=remaining,
            daily_credit_limit=settings.DAILY_CREDITS_LIMIT,
            credit_costs={
                "main": settings.CREDIT_COST_MAIN_ASSISTANT,
                "soliq": settings.CREDIT_COST_SOLIQ_ASSISTANT,
                "deepresearch": settings.CREDIT_COST_DEEPRESEARCH,
            },
        )
    except Exception as e:
        logger.error(
            f"[AdminAPI] Error getting rate limit for user {user_id}: {str(e)}"
        )
        raise HTTPException(
            status_code=500, detail="Failed to retrieve rate limit information"
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
                "message": f"Rate limit reset successfully for user {request.user_id}",
            }
        else:
            return {
                "success": False,
                "message": f"No rate limit record found for user {request.user_id} today",
            }
    except Exception as e:
        logger.error(
            f"[AdminAPI] Error resetting rate limit for user {request.user_id}: {str(e)}"
        )
        raise HTTPException(status_code=500, detail="Failed to reset rate limit")


#  Promo Code Management Endpoints


@router.post("/promo-codes", summary="Create a new promo code")
async def create_promo_code(request: PromoCodeCreate, created_by: str = "admin"):
    """
    Create a new promo code for unlimited access.

    Parameters:
    - code: Unique promo code string
    - description: Optional description
    - created_by: Admin user ID creating the code

    Returns:
    - success: Whether creation was successful
    - message: Status message
    - promo_code: The created promo code details
    """
    try:
        success = promo_code_service.create_promo_code(
            code=request.code,
            created_by=created_by,
            description=request.description,
            expiration_date=request.expiration_date,
            credit_amount=request.credit_amount,
        )

        if success:
            promo_code = promo_code_service.get_promo_code(request.code)
            return {
                "success": True,
                "message": f"Promo code '{request.code}' created successfully",
                "promo_code": {
                    "code": promo_code["code"],
                    "is_active": promo_code["is_active"],
                    "expiration_date": promo_code.get("expiration_date"),
                    "credit_amount": promo_code.get("credit_amount"),
                    "created_at": promo_code["created_at"],
                    "created_by": promo_code.get("created_by"),
                    "description": promo_code.get("description"),
                },
            }
        else:
            raise HTTPException(
                status_code=400, detail=f"Promo code '{request.code}' already exists"
            )
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"[AdminAPI] Error creating promo code: {str(e)}")
        raise HTTPException(status_code=500, detail="Failed to create promo code")


@router.get("/promo-codes", summary="List all promo codes")
async def list_promo_codes(active_only: bool = False):
    """
    List all promo codes.

    Parameters:
    - active_only: If true, return only active promo codes

    Returns:
    - promo_codes: List of promo codes
    - count: Total number of promo codes
    """
    try:
        promo_codes = promo_code_service.list_all_promo_codes(active_only=active_only)

        return {
            "promo_codes": [
                {
                    "code": pc["code"],
                    "is_active": pc["is_active"],
                    "expiration_date": pc.get("expiration_date"),
                    "credit_amount": pc.get("credit_amount"),
                    "created_at": pc["created_at"],
                    "created_by": pc.get("created_by"),
                    "description": pc.get("description"),
                }
                for pc in promo_codes
            ],
            "count": len(promo_codes),
        }
    except Exception as e:
        logger.error(f"[AdminAPI] Error listing promo codes: {str(e)}")
        raise HTTPException(status_code=500, detail="Failed to retrieve promo codes")


@router.get("/promo-codes/{code}", summary="Get specific promo code details")
async def get_promo_code(code: str):
    """
    Get details of a specific promo code.

    Parameters:
    - code: The promo code to retrieve

    Returns:
    - Promo code details
    """
    try:
        promo_code = promo_code_service.get_promo_code(code)

        if not promo_code:
            raise HTTPException(
                status_code=404, detail=f"Promo code '{code}' not found"
            )

        return {
            "code": promo_code["code"],
            "is_active": promo_code["is_active"],
            "expiration_date": promo_code.get("expiration_date"),
            "credit_amount": promo_code.get("credit_amount"),
            "created_at": promo_code["created_at"],
            "created_by": promo_code.get("created_by"),
            "description": promo_code.get("description"),
        }
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"[AdminAPI] Error getting promo code: {str(e)}")
        raise HTTPException(status_code=500, detail="Failed to retrieve promo code")


@router.patch("/promo-codes/{code}/activate", summary="Activate a promo code")
async def activate_promo_code(code: str):
    """
    Activate a promo code.

    Parameters:
    - code: The promo code to activate

    Returns:
    - success: Whether activation was successful
    - message: Status message
    """
    try:
        success = promo_code_service.activate_promo_code(code)

        if success:
            return {
                "success": True,
                "message": f"Promo code '{code}' activated successfully",
            }
        else:
            raise HTTPException(
                status_code=404, detail=f"Promo code '{code}' not found"
            )
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"[AdminAPI] Error activating promo code: {str(e)}")
        raise HTTPException(status_code=500, detail="Failed to activate promo code")


@router.patch("/promo-codes/{code}/deactivate", summary="Deactivate a promo code")
async def deactivate_promo_code(code: str):
    """
    Deactivate a promo code.

    Parameters:
    - code: The promo code to deactivate

    Returns:
    - success: Whether deactivation was successful
    - message: Status message
    """
    try:
        success = promo_code_service.deactivate_promo_code(code)

        if success:
            return {
                "success": True,
                "message": f"Promo code '{code}' deactivated successfully",
            }
        else:
            raise HTTPException(
                status_code=404, detail=f"Promo code '{code}' not found"
            )
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"[AdminAPI] Error deactivating promo code: {str(e)}")
        raise HTTPException(status_code=500, detail="Failed to deactivate promo code")


@router.delete("/promo-codes/{code}", summary="Delete a promo code")
async def delete_promo_code(code: str):
    """
    Delete a promo code and all user assignments.

    Parameters:
    - code: The promo code to delete

    Returns:
    - success: Whether deletion was successful
    - message: Status message
    """
    try:
        success = promo_code_service.delete_promo_code(code)

        if success:
            return {
                "success": True,
                "message": f"Promo code '{code}' deleted successfully",
            }
        else:
            raise HTTPException(
                status_code=404, detail=f"Promo code '{code}' not found"
            )
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"[AdminAPI] Error deleting promo code: {str(e)}")
        raise HTTPException(status_code=500, detail="Failed to delete promo code")


# User Promo Code Assignment Endpoints


@router.post("/promo-codes/assign", summary="Assign promo code to a user")
async def assign_promo_code_to_user(request: UserPromoCode):
    """
    Assign a promo code to a user for unlimited access.

    Parameters:
    - user_id: The user ID to assign promo code to
    - promo_code: The promo code to assign

    Returns:
    - success: Whether assignment was successful
    - message: Status message
    """
    try:
        success, status_code = promo_code_service.assign_promo_code_to_user(
            user_id=request.user_id, promo_code=request.promo_code
        )

        if success:
            return {
                "success": True,
                "message": f"Promo code '{request.promo_code}' assigned to user {request.user_id}",
                "user_id": request.user_id,
                "promo_code": request.promo_code,
                "has_unlimited_access": True,
            }
        else:
            raise HTTPException(
                status_code=status_code,
                detail="Failed to assign promo code. Code may not exist or is inactive.",
            )
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"[AdminAPI] Error assigning promo code: {str(e)}")
        raise HTTPException(status_code=500, detail="Failed to assign promo code")


@router.delete("/promo-codes/assign/{user_id}", summary="Remove promo code from user")
async def remove_user_promo_code(user_id: str):
    """
    Remove promo code from a user.

    Parameters:
    - user_id: The user ID to remove promo code from

    Returns:
    - success: Whether removal was successful
    - message: Status message
    """
    try:
        success = promo_code_service.remove_user_promo_code(user_id)

        if success:
            return {
                "success": True,
                "message": f"Promo code removed from user {user_id}",
            }
        else:
            raise HTTPException(
                status_code=404,
                detail=f"No promo code assignment found for user {user_id}",
            )
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"[AdminAPI] Error removing user promo code: {str(e)}")
        raise HTTPException(status_code=500, detail="Failed to remove promo code")


@router.get("/promo-codes/users", summary="List all users with promo codes")
async def list_users_with_promo_codes():
    """
    List all users who have promo code assignments.

    Returns:
    - users: List of user promo code assignments
    - count: Total number of users with promo codes
    """
    try:
        assignments = promo_code_service.list_users_with_promo_codes()

        return {
            "users": [
                {
                    "user_id": assignment["user_id"],
                    "promo_code": assignment["promo_code"],
                    "assigned_at": assignment["assigned_at"],
                    "has_unlimited_access": assignment.get(
                        "has_unlimited_access", True
                    ),
                }
                for assignment in assignments
            ],
            "count": len(assignments),
        }
    except Exception as e:
        logger.error(f"[AdminAPI] Error listing users with promo codes: {str(e)}")
        raise HTTPException(
            status_code=500, detail="Failed to retrieve user promo code assignments"
        )


@router.get("/promo-codes/users/{user_id}", summary="Get user's promo code")
async def get_user_promo_code(user_id: str):
    """
    Get the promo code assigned to a specific user.

    Parameters:
    - user_id: The user ID to check

    Returns:
    - User's promo code assignment details
    """
    try:
        assignment = promo_code_service.get_user_promo_code(user_id)

        if not assignment:
            raise HTTPException(
                status_code=404, detail=f"No promo code found for user {user_id}"
            )

        return {
            "user_id": assignment["user_id"],
            "promo_code": assignment["promo_code"],
            "assigned_at": assignment["assigned_at"],
            "has_unlimited_access": assignment.get("has_unlimited_access", True),
        }
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"[AdminAPI] Error getting user promo code: {str(e)}")
        raise HTTPException(
            status_code=500, detail="Failed to retrieve user promo code"
        )
