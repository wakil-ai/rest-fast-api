# app/routers/history/messages.py
import secrets

from fastapi import APIRouter, HTTPException, Query, Request, status
from fastapi.responses import Response

from core.dependencies import get_chat_history_service
from models.chat_history import (
    MessageCreateRequest,
    MessageResponse,
    MessageSharedRequest,
    MessageSharedResponse,
)
from utils.message_export import build_message_docx
from utils.user_management import (
    handle_service_error,
    sanitize_message_for_response,
    serialize_mongo_id,
)

router = APIRouter(prefix="/messages", tags=["Messages"])

# Only DOCX is available for now; the query param is already shaped for a
# future `format=pdf` so the frontend contract won't need to change later.
_SUPPORTED_DOWNLOAD_FORMATS = {"docx"}

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


@router.get("/{message_id}/download")
@handle_service_error
async def download_message(
    request: Request,
    message_id: str,
    format: str = Query("docx", description="Download format (only 'docx' for now)"),
):
    """Download a message's query + response as a rendered document."""
    if format not in _SUPPORTED_DOWNLOAD_FORMATS:
        raise HTTPException(
            400,
            f"Unsupported format '{format}'. Supported formats: "
            f"{sorted(_SUPPORTED_DOWNLOAD_FORMATS)}",
        )

    msg = await chat_history_service.get_message(message_id)
    if not msg:
        raise HTTPException(404, "Message not found")

    authenticated_user_id = getattr(request.state, "authenticated_user_id", None)
    message_owner_id = msg.get("user_id")
    if (
        authenticated_user_id
        and message_owner_id
        and not secrets.compare_digest(
            str(authenticated_user_id), str(message_owner_id)
        )
    ):
        raise HTTPException(403, "Message does not belong to this user")

    file_bytes, content_type, filename = build_message_docx(msg)
    return Response(
        content=file_bytes,
        media_type=content_type,
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@router.get("/{session_id}/{message_id}", response_model=MessageResponse)
@handle_service_error
async def get_message(session_id: str, message_id: str):
    msg = await chat_history_service.get_message(message_id)
    if not msg:
        raise HTTPException(404, "Message not found")
    if msg["session_id"] != session_id:
        raise HTTPException(403, "Message does not belong to this session")
    return sanitize_message_for_response(serialize_mongo_id(msg))


@router.post("/{message_id}/share", response_model=MessageSharedResponse)
@handle_service_error
async def share_message(message_id: str, request: MessageSharedRequest):
    share = await chat_history_service.share_message(
        message_id=message_id, user_id=request.user_id
    )
    return serialize_mongo_id(share)
