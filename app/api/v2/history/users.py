import json

from fastapi import APIRouter, Depends, HTTPException, Response, status

from app.core.config import settings
from app.core.dependencies import get_chat_history_service, get_redis_service
from app.models.chat_history import (
    UserCreateRequest,
    UserCreateResponse,
    UserPhoneUpdateRequest,
    UserUpdateRequest,
)
from app.security import verify_super_admin_key
from app.utils.user_management import handle_service_error, serialize_mongo_id

router = APIRouter(prefix="/users", tags=["Users"])

chat_history_service = get_chat_history_service()
redis_service = get_redis_service()


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
    response.status_code = status.HTTP_201_CREATED
    return create_response(user, "User created successfully")


@router.get("/{user_id}", response_model=UserCreateResponse)
@handle_service_error
async def get_user(user_id: str):
    cache_key = f"user:{user_id}"

    # Try to get from Redis cache first
    cached_user = redis_service.cache_get(cache_key)
    if cached_user:
        user = json.loads(cached_user)
        return create_response(user, "User retrieved (from cache)")

    # Cache miss - fetch from database
    user = await chat_history_service.get_user(user_id=user_id)
    if not user:
        raise HTTPException(404, f"User {user_id} not found")

    # Cache the user with 1 day TTL (cache_set handles datetime serialization)
    redis_service.cache_set(cache_key, user, ttl_seconds=86400)
    return create_response(user, "User retrieved")


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
    return create_response(user, "User unblocked successfully")
