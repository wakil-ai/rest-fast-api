from typing import Any, cast

from fastapi import APIRouter, Depends, HTTPException

from app.core.dependencies import get_promo_code_service
from app.core.logger import logger
from app.models.promo_code import PromoCodeCreate, UserPromoCode
from app.security import verify_api_key, verify_api_key_or_dt_key

router = APIRouter(prefix="/promo-codes", tags=["Promo Codes"])

admin_only = [Depends(verify_api_key)]
shared_api_access = [Depends(verify_api_key_or_dt_key)]

promo_code_service = get_promo_code_service()


@router.post("", summary="Create a new promo code", dependencies=admin_only)
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
        success = await promo_code_service.create_promo_code(
            code=request.code,
            created_by=created_by,
            description=request.description,
            expiration_date=request.expiration_date,
            credit_amount=request.credit_amount,
        )

        if success:
            promo_code = await promo_code_service.get_promo_code(request.code)
            if not promo_code:
                raise HTTPException(
                    status_code=500, detail="Failed to load created promo code"
                )
            promo_code = cast(dict[str, Any], promo_code)
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
        logger.error(f"[PromoCodesAPI] Error creating promo code: {str(e)}")
        raise HTTPException(status_code=500, detail="Failed to create promo code")


@router.get(
    "/{code}",
    summary="Get specific promo code details",
    dependencies=admin_only,
)
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
        promo_code = cast(dict[str, Any], promo_code)

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
        logger.error(f"[PromoCodesAPI] Error getting promo code: {str(e)}")
        raise HTTPException(status_code=500, detail="Failed to retrieve promo code")


@router.patch(
    "/{code}/activate",
    summary="Activate a promo code",
    dependencies=admin_only,
)
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
        logger.error(f"[PromoCodesAPI] Error activating promo code: {str(e)}")
        raise HTTPException(status_code=500, detail="Failed to activate promo code")


@router.patch(
    "/{code}/deactivate",
    summary="Deactivate a promo code",
    dependencies=admin_only,
)
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
        logger.error(f"[PromoCodesAPI] Error deactivating promo code: {str(e)}")
        raise HTTPException(status_code=500, detail="Failed to deactivate promo code")


@router.post(
    "/assign",
    summary="Assign promo code to a user",
    dependencies=shared_api_access,
)
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
        logger.error(f"[PromoCodesAPI] Error assigning promo code: {str(e)}")
        raise HTTPException(status_code=500, detail="Failed to assign promo code")


@router.delete(
    "/assign/{user_id}",
    summary="Remove promo code from user",
    dependencies=shared_api_access,
)
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
        success = await promo_code_service.remove_user_promo_code(user_id)

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
        logger.error(f"[PromoCodesAPI] Error removing user promo code: {str(e)}")
        raise HTTPException(status_code=500, detail="Failed to remove promo code")


@router.get(
    "/users/{user_id}",
    summary="Get user's promo code",
    dependencies=shared_api_access,
)
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
        assignment = cast(dict[str, Any], assignment)

        return {
            "user_id": assignment["user_id"],
            "promo_code": assignment["promo_code"],
            "assigned_at": assignment["assigned_at"],
            "has_unlimited_access": assignment.get("has_unlimited_access", True),
        }
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"[PromoCodesAPI] Error getting user promo code: {str(e)}")
        raise HTTPException(
            status_code=500, detail="Failed to retrieve user promo code"
        )
