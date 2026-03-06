from fastapi import APIRouter, HTTPException

from app.core.config import settings
from app.core.dependencies import get_promo_code_service, get_rate_limit_service
from app.core.logger import logger
from app.models.promo_code import PromoCodeCreate, UserPromoCode
from app.models.rate_limit import RateLimitResponse

router = APIRouter(prefix="/admin", tags=["Admin"])

# Initialize services
rate_limit_service = get_rate_limit_service()
promo_code_service = get_promo_code_service()


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
        remaining = await rate_limit_service.get_remaining_credits(user_id)
        daily_limit = await rate_limit_service.get_daily_credit_limit(user_id)
        return RateLimitResponse(
            user_id=user_id,
            remaining_credits=remaining,
            daily_credit_limit=daily_limit,
            credit_costs={
                "main": settings.CREDIT_COST_MAIN_ASSISTANT,
                "tax": settings.CREDIT_COST_SOLIQ_ASSISTANT,
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
        if request.super_secret_admin_key != settings.SUPER_ADMIN_API_KEY:
            raise HTTPException(status_code=403, detail="Invalid Admin key")

        success = await promo_code_service.create_promo_code(
            code=request.code,
            created_by=created_by,
            description=request.description,
            expiration_date=request.expiration_date,
            credit_amount=request.credit_amount,
        )

        if success:
            promo_code = await promo_code_service.get_promo_code(request.code)
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
        promo_code = await promo_code_service.get_promo_code(code)

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
        success = await promo_code_service.activate_promo_code(code)

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
        success = await promo_code_service.deactivate_promo_code(code)

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
        success, status_code = await promo_code_service.assign_promo_code_to_user(
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
        assignment = await promo_code_service.get_user_promo_code(user_id)

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
