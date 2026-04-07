# app/routers/history/messages.py
from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel

from app.core.dependencies import get_chat_history_service
from app.models.chat_history import (
    MessageCreateRequest,
    MessageResponse,
    MessageSharedRequest,
    MessageSharedResponse,
)
from app.utils.user_management import (
    handle_service_error,
    sanitize_message_for_response,
    serialize_mongo_id,
)


class DeleteMessagesFromResponse(BaseModel):
    deleted_count: int

router = APIRouter(prefix="/messages", tags=["Messages"])

chat_history_service = get_chat_history_service()


@router.post("", status_code=status.HTTP_201_CREATED, response_model=MessageResponse)
@handle_service_error
async def create_message(request: MessageCreateRequest):
    """Create a new message. Message ID is auto-generated (client-provided ID is ignored)."""
    msg = await chat_history_service.add_message(
        session_id=request.session_id,
        file_ids=request.file_ids,
        content=request.content,
        metadata=request.metadata,
    )
    return MessageResponse(**sanitize_message_for_response(serialize_mongo_id(msg)))


@router.get("/{session_id}", response_model=list[MessageResponse])
@handle_service_error
async def list_messages(session_id: str, limit: int = 50):
    msgs = await chat_history_service.get_messages(session_id, limit)
    return [sanitize_message_for_response(serialize_mongo_id(m)) for m in msgs]


@router.get("/{session_id}/{message_id}", response_model=MessageResponse)
@handle_service_error
async def get_message(session_id: str, message_id: str):
    msg = await chat_history_service.get_message(message_id)
    if not msg:
        raise HTTPException(404, "Message not found")
    if msg["session_id"] != session_id:
        raise HTTPException(403, "Message does not belong to this session")
    return sanitize_message_for_response(serialize_mongo_id(msg))


@router.delete(
    "/{session_id}/from/{message_id}",
    response_model=DeleteMessagesFromResponse,
)
@handle_service_error
async def delete_messages_from(session_id: str, message_id: str):
    """Delete all messages in a session starting from message_id (inclusive)."""
    deleted_count = await chat_history_service.delete_messages_from(session_id, message_id)
    return DeleteMessagesFromResponse(deleted_count=deleted_count)


@router.post("/{message_id}/share", response_model=MessageSharedResponse)
@handle_service_error
async def share_message(message_id: str, request: MessageSharedRequest):
    share = await chat_history_service.share_message(
        message_id=message_id, user_id=request.user_id
    )
    return serialize_mongo_id(share)
