# app/routers/history/messages.py
from fastapi import APIRouter, HTTPException, status

from app.models.chat_history import (
    MessageCreateRequest, MessageCreateResponse, MessageResponse
)
from app.services.chat_history_service import ChatHistoryService
from app.utils.user_management import handle_service_error, serialize_mongo_id

router = APIRouter(prefix="/messages", tags=["Messages"])

chat_history_service = ChatHistoryService()

@router.post("", status_code=status.HTTP_201_CREATED, response_model=MessageCreateResponse)
@handle_service_error
async def create_message(request: MessageCreateRequest):
    # Auto-create session if missing
    if not await chat_history_service.get_session(request.session_id):
        user_id = (
            request.user_id
            or (request.metadata or {}).get("user_id")
            or (request.metadata or {}).get("userId")
            or "anonymous"
        )
        await chat_history_service.create_session(
            user_id=user_id, session_id=request.session_id
        )

    msg = await chat_history_service.add_message(
        session_id=request.session_id,
        message_id=request.message_id,
        content=request.content,
        metadata=request.metadata,
    )
    return {"info": serialize_mongo_id(msg), "message": "Message added"}


@router.get("/{session_id}", response_model=list[MessageResponse])
@handle_service_error
async def list_messages(session_id: str, limit: int = 100):
    msgs = await chat_history_service.get_messages(session_id, limit)
    return [serialize_mongo_id(m) for m in msgs]


@router.get("/{session_id}/{message_id}", response_model=MessageResponse)
@handle_service_error
async def get_message(session_id: str, message_id: str):
    msg = await chat_history_service.get_message(message_id)
    if not msg:
        raise HTTPException(404, "Message not found")
    if msg["session_id"] != session_id:
        raise HTTPException(403, "Message does not belong to this session")
    return serialize_mongo_id(msg)