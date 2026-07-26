import json

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status

from core.config import settings
from core.dependencies import (
    get_account_archive_service,
    get_chat_history_service,
    get_rate_limit_service,
    get_redis_service,
)
from core.logger import logger
from models.chat_history import (
    AccountDeletionRequest,
    AccountDeletionResponse,
    UserCreateRequest,
    UserCreateResponse,
    UserPhoneUpdateRequest,
    UserUpdateRequest,
)
from models.rate_limit import RateLimitResponse
from security import invalidate_user_auth_cache, verify_super_admin_key
from utils.user_management import handle_service_error, serialize_mongo_id

router = APIRouter(prefix="/users", tags=["Users"])

chat_history_service = get_chat_history_service()
redis_service = get_redis_service()
rate_limit_service = get_rate_limit_service()
account_archive_service = get_account_archive_service()


def create_response(data: dict, message: str) -> dict:
    return {"info": serialize_mongo_id(data), "message": message}


@router.post(
    "",
    status_code=status.HTTP_200_OK,
    response_model=UserCreateResponse,
    dependencies=[Depends(verify_super_admin_key)],
)
@handle_service_error
async def create_or_get_user(request: UserCreateRequest, response: Response):
    """Create a new user or return existing one (idempotent)"""
    existing = await chat_history_service.get_user(user_id=request.user_id)
    if existing:
        invalidate_user_auth_cache(request.user_id)
        return create_response(existing, "User already exists")

    user = await chat_history_service.create_user(
        user_id=request.user_id,
        username=request.username,
        first_name=request.first_name,
        phone_number=request.phone_number,
        last_name=request.last_name,
        picture=request.picture,
        web_client=settings.WAKILAI_WEB_CLIENT_NAME,
    )
    invalidate_user_auth_cache(request.user_id)
    response.status_code = status.HTTP_201_CREATED
    return create_response(user, "User created successfully")


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
            f"[UsersAPI] Error getting rate limit for user {user_id}: {str(e)}"
        )
        raise HTTPException(
            status_code=500, detail="Failed to retrieve rate limit information"
        )


@router.get("/{user_id}", response_model=UserCreateResponse)
@handle_service_error
async def get_user(user_id: str, request: Request):
    default_api_key = request.headers.get(settings.API_KEY_NAME.lower())
    dt_api_key = request.headers.get(settings.DT_API_KEY_NAME.lower())

    requested_web_client = settings.WAKILAI_WEB_CLIENT_NAME  # Default to WAKILAI_WEB_CLIENT_NAME

    if dt_api_key:
        requested_web_client = settings.DT_WEB_CLIENT_NAME

    cache_key = f"user:{user_id}"

    # Try to get from Redis cache first
    cached_user = redis_service.cache_get(cache_key)
    if cached_user:
        user = json.loads(cached_user)

        if user.get("web_client") != requested_web_client:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Forbidden: User does not belong to the requested client",
            )

        return create_response(user, "User retrieved (from cache)")

    # Cache miss - fetch from database
    user = await chat_history_service.get_user(user_id=user_id)
    if not user:
        raise HTTPException(404, f"User {user_id} not found")

    if user.get("web_client") != requested_web_client:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Forbidden: User does not belong to the requested client",
        )

    # Cache the user with 1 day TTL (cache_set handles datetime serialization)
    redis_service.cache_set(cache_key, user, ttl_seconds=86400)
    return create_response(user, "User retrieved")


@router.post(
    "/{user_id}/delete-account",
    status_code=status.HTTP_200_OK,
    response_model=AccountDeletionResponse,
    summary="Request account deletion (soft-delete / archive)",
    responses={
        200: {"description": "Account archived (or already archived — idempotent)."},
        400: {"description": "Missing or blank user_id."},
        404: {"description": "No user exists with that user_id."},
    },
)
@handle_service_error
async def delete_account(user_id: str, request: AccountDeletionRequest | None = None):
    """Archive a user account and all owned data for a deletion request.

    Backs the Google Play "delete my account" URL. This is a soft-delete: the user
    and all owned data are stamped ``archived`` in place (see
    ``AccountArchiveService``); financial/tax records follow a separate 1-year
    retention job. Deletion is permanent for that login identity — an archived
    account can no longer log in or sign up.

    Idempotent: repeating the call on an already-archived account returns 200 with
    ``already_archived=True`` rather than an error. Not guarded by
    ``verify_not_archived`` so an already-archived user still gets this answer.
    """
    reason = request.reason if request and request.reason else "user_request"
    result = await account_archive_service.archive_user_account(user_id, reason=reason)

    # Drop any cached copy so subsequent reads don't serve the pre-archive doc.
    redis_service.invalidate_cache(f"user:{user_id}")
    invalidate_user_auth_cache(user_id)

    already = result["already_archived"]
    return AccountDeletionResponse(
        user_id=result["user_id"],
        status="archived",
        already_archived=already,
        archived_at=result.get("archived_at"),
        message=(
            "Account was already archived"
            if already
            else "Account archived successfully"
        ),
    )


@router.patch(
    "/phone-number", status_code=status.HTTP_200_OK, response_model=UserCreateResponse
)
@handle_service_error
async def update_user_phone_number(request: UserPhoneUpdateRequest):
    user = await chat_history_service.update_user_phone_number(
        user_id=request.user_id,
        phone_number=request.phone_number,
    )
    if not user:
        raise HTTPException(404, f"User {request.user_id} not found")

    # Invalidate cache
    redis_service.invalidate_cache(f"user:{request.user_id}")
    return create_response(user, "User phone number updated")


# Endpoint for changing user information
@router.patch(
    "/change/info/{user_id}",
    status_code=status.HTTP_200_OK,
    response_model=UserCreateResponse,
)
@handle_service_error
async def update_user_info(user_id: str, request: UserUpdateRequest):
    user = await chat_history_service.update_user_info(
        user_id=user_id,
        field=request.field,
        value=request.value,
    )
    if not user:
        raise HTTPException(404, f"User {user_id} not found")

    # Invalidate cache
    redis_service.invalidate_cache(f"user:{user_id}")
    invalidate_user_auth_cache(user_id)
    return create_response(user, "User information updated")


# Endpoint for blocking a user
@router.patch(
    "/block/{user_id}",
    status_code=status.HTTP_200_OK,
    response_model=UserCreateResponse,
    dependencies=[Depends(verify_super_admin_key)],
)
@handle_service_error
async def block_user(user_id: str, reason: str | None = None):
    user = await chat_history_service.block_user(user_id=user_id, reason=reason)
    if not user:
        raise HTTPException(404, f"User {user_id} not found")

    # Invalidate cache
    redis_service.invalidate_cache(f"user:{user_id}")
    invalidate_user_auth_cache(user_id)
    return create_response(user, "User blocked successfully")


@router.patch(
    "/unblock/{user_id}",
    status_code=status.HTTP_200_OK,
    response_model=UserCreateResponse,
    dependencies=[Depends(verify_super_admin_key)],
)
@handle_service_error
async def unblock_user(user_id: str):
    user = await chat_history_service.unblock_user(user_id=user_id)
    if not user:
        raise HTTPException(404, f"User {user_id} not found")

    # Invalidate cache
    redis_service.invalidate_cache(f"user:{user_id}")
    invalidate_user_auth_cache(user_id)
    return create_response(user, "User unblocked successfully")
