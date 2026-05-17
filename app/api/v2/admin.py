import time
from typing import Any, cast

from fastapi import APIRouter, Depends, HTTPException
from pymongo import UpdateOne

from app.core.config import settings
from app.core.dependencies import (
    get_mongo_handler,
    get_promo_code_service,
    get_rate_limit_service,
)
from app.core.logger import logger
from app.models.promo_code import PromoCodeCreate, UserPromoCode
from app.models.rate_limit import RateLimitResponse
from app.models.telegram import (
    TelegramChatEntry,
    TelegramChatsListResponse,
    TelegramChatsSaveRequest,
)
from app.security import verify_api_key, verify_api_key_or_dt_key

router = APIRouter(prefix="/admin", tags=["Admin"])

admin_only = [Depends(verify_api_key)]
shared_api_access = [Depends(verify_api_key_or_dt_key)]

# Initialize services
rate_limit_service = get_rate_limit_service()
promo_code_service = get_promo_code_service()
mongo_handler = get_mongo_handler()


def _require_admin_key(key: str) -> None:
    if key != settings.SUPER_ADMIN_API_KEY:
        raise HTTPException(status_code=403, detail="Invalid Admin key")


@router.get(
    "/rate-limit/{user_id}",
    response_model=RateLimitResponse,
    summary="Get user's remaining credits",
    dependencies=shared_api_access,
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
        status = await rate_limit_service.get_credit_status(user_id)
        return RateLimitResponse(
            user_id=user_id,
            remaining_credits=int(status["remaining_credits"]),
            daily_credit_limit=int(status["effective_daily_credit_limit"]),
            effective_daily_credit_limit=int(status["effective_daily_credit_limit"]),
            today_credits_used=int(status["today_credits_used"]),
            uses_combined_credit_pool=bool(status["uses_combined_credit_pool"]),
        )
    except Exception as e:
        logger.error(
            f"[AdminAPI] Error getting rate limit for user {user_id}: {str(e)}"
        )
        raise HTTPException(
            status_code=500, detail="Failed to retrieve rate limit information"
        )


#  Promo Code Management Endpoints
@router.post("/promo-codes", summary="Create a new promo code", dependencies=admin_only)
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
        _require_admin_key(request.super_secret_admin_key)

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
        logger.error(f"[AdminAPI] Error creating promo code: {str(e)}")
        raise HTTPException(status_code=500, detail="Failed to create promo code")


@router.get(
    "/promo-codes/{code}",
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
        logger.error(f"[AdminAPI] Error getting promo code: {str(e)}")
        raise HTTPException(status_code=500, detail="Failed to retrieve promo code")


@router.patch(
    "/promo-codes/{code}/activate",
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
        logger.error(f"[AdminAPI] Error activating promo code: {str(e)}")
        raise HTTPException(status_code=500, detail="Failed to activate promo code")


@router.patch(
    "/promo-codes/{code}/deactivate",
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
        logger.error(f"[AdminAPI] Error deactivating promo code: {str(e)}")
        raise HTTPException(status_code=500, detail="Failed to deactivate promo code")


# User Promo Code Assignment Endpoints
@router.post(
    "/promo-codes/assign",
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
        logger.error(f"[AdminAPI] Error assigning promo code: {str(e)}")
        raise HTTPException(status_code=500, detail="Failed to assign promo code")


@router.delete(
    "/promo-codes/assign/{user_id}",
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
        logger.error(f"[AdminAPI] Error removing user promo code: {str(e)}")
        raise HTTPException(status_code=500, detail="Failed to remove promo code")


@router.get(
    "/promo-codes/users/{user_id}",
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
        logger.error(f"[AdminAPI] Error getting user promo code: {str(e)}")
        raise HTTPException(
            status_code=500, detail="Failed to retrieve user promo code"
        )


@router.post(
    "/telegram/save/chats",
    summary="Save Telegram chat ids (upsert)",
    dependencies=admin_only,
)
async def save_telegram_chats(request: TelegramChatsSaveRequest):
    try:
        _require_admin_key(request.super_secret_admin_key)

        records = []
        if request.chat is not None:
            records.append(request.chat)
        if request.chats:
            records.extend(request.chats)

        if not records:
            raise HTTPException(status_code=400, detail="chat(s) required")

        # Deduplicate by (chat_id, user_id)
        dedup: dict[str, Any] = {}
        for r in records:
            key = f"{int(r.chat_id)}:{int(r.user_id)}"
            dedup[key] = r
        records = list(dedup.values())

        now_ms = int(time.time() * 1000)
        collection = mongo_handler.db[settings.TELEGRAM_CHATS_COLLECTION]

        # Best-effort: keep (chat_id, user_id) unique.
        try:
            await collection.create_index([("chat_id", 1), ("user_id", 1)], unique=True)
            await collection.create_index("chat_id")
            await collection.create_index("user_id")
        except Exception:
            pass

        ops: list[UpdateOne] = []
        for r in records:
            chat_id = int(r.chat_id)
            user_id = int(r.user_id)
            update_set: dict[str, Any] = {
                "chat_id": chat_id,
                "user_id": user_id,
                "is_active": True,
                "updated_at_ms": now_ms,
            }
            if getattr(r, "username", None):
                update_set["username"] = r.username
            if getattr(r, "first_name", None):
                update_set["first_name"] = r.first_name
            if getattr(r, "last_name", None):
                update_set["last_name"] = r.last_name
            if getattr(r, "language_code", None):
                update_set["language_code"] = r.language_code

            ops.append(
                UpdateOne(
                    {"chat_id": chat_id, "user_id": user_id},
                    {
                        "$set": update_set,
                        "$setOnInsert": {"created_at_ms": now_ms},
                    },
                    upsert=True,
                )
            )

        result = await collection.bulk_write(ops, ordered=False)

        return {
            "success": True,
            "received": len(records),
            "matched": int(getattr(result, "matched_count", 0) or 0),
            "modified": int(getattr(result, "modified_count", 0) or 0),
            "upserted": int(len(getattr(result, "upserted_ids", {}) or {})),
        }
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"[AdminAPI] Error saving telegram chats: {str(e)}")
        raise HTTPException(status_code=500, detail="Failed to save telegram chats")


@router.get(
    "/telegram/chats",
    response_model=TelegramChatsListResponse,
    summary="List Telegram chat ids",
    dependencies=admin_only,
)
async def list_telegram_chats(
    super_secret_admin_key: str,
    active_only: bool = True,
):
    try:
        _require_admin_key(super_secret_admin_key)

        query = {"is_active": True} if active_only else {}
        projection = {
            "_id": 0,
            "chat_id": 1,
            "user_id": 1,
            "is_active": 1,
            "created_at_ms": 1,
            "updated_at_ms": 1,
        }
        collection = mongo_handler.db[settings.TELEGRAM_CHATS_COLLECTION]

        cursor = collection.find(query, projection).sort("updated_at_ms", -1)
        docs = await cursor.to_list(length=None)
        chats: list[TelegramChatEntry] = []
        for d in docs:
            if d.get("chat_id") is None or d.get("user_id") is None:
                continue
            chats.append(
                TelegramChatEntry(
                    chat_id=int(d["chat_id"]),
                    user_id=int(d["user_id"]),
                    is_active=bool(d.get("is_active", True)),
                    created_at_ms=(
                        int(d["created_at_ms"])
                        if d.get("created_at_ms") is not None
                        else None
                    ),
                    updated_at_ms=(
                        int(d["updated_at_ms"])
                        if d.get("updated_at_ms") is not None
                        else None
                    ),
                )
            )

        return TelegramChatsListResponse(chats=chats, count=len(chats))
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"[AdminAPI] Error listing telegram chats: {str(e)}")
        raise HTTPException(status_code=500, detail="Failed to list telegram chats")
