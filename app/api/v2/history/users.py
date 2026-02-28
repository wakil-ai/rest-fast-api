from fastapi import APIRouter, HTTPException, Response, status

from app.core.dependencies import get_chat_history_service
from app.models.chat_history import (
    UserCreateRequest,
    UserCreateResponse,
    UserPhoneUpdateRequest,
    UserUpdateRequest,
)
from app.utils.user_management import handle_service_error, serialize_mongo_id

router = APIRouter(prefix="/users", tags=["Users"])

chat_history_service = get_chat_history_service()


def create_response(data: dict, message: str) -> dict:
    return {"info": serialize_mongo_id(data), "message": message}


@router.post("", status_code=status.HTTP_200_OK, response_model=UserCreateResponse)
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
        last_name=request.last_name,
        picture=request.picture,
    )
    response.status_code = status.HTTP_201_CREATED
    return create_response(user, "User created successfully")


@router.get("/{user_id}", response_model=UserCreateResponse)
@handle_service_error
async def get_user(user_id: str):
    user = await chat_history_service.get_user(user_id=user_id)
    if not user:
        raise HTTPException(404, f"User {user_id} not found")
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
    return create_response(user, "User phone number updated")


# Endpoint for changing user information 
@router.patch(
    "/change/info/{user_id}", status_code=status.HTTP_200_OK, response_model=UserCreateResponse
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
    return create_response(user, "User information updated")