# app/routers/history/messages.py
import asyncio

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.responses import Response

from core.dependencies import get_chat_history_service
from models.chat_history import (
    MessageCreateRequest,
    MessageResponse,
    MessageSharedRequest,
    MessageSharedResponse,
)
from security.dependencies import get_current_user_id
from utils.message_export import (
    DOCX_CONTENT_TYPE,
    DOCX_FILENAME,
    build_message_docx,
    message_markdown,
)
from utils.user_management import (
    handle_service_error,
    sanitize_message_for_response,
    serialize_mongo_id,
)

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


@router.get("/{message_id}/docx")
@handle_service_error
async def download_message_docx(
    message_id: str, user_id: str = Depends(get_current_user_id)
):
    """Render a message as a .docx for download.

    Registered before /{session_id}/{message_id} below: both are two-segment
    GET paths, and Starlette matches routes in registration order, so this
    literal "docx" tail would otherwise never be reached — every request
    would match get_message first with message_id="docx".

    Rendered by pandoc (utils/message_export.py) so the tables the court and
    contract prompts mandate survive as real Word tables.
    """
    msg = await chat_history_service.get_message(message_id)
    if not msg:
        raise HTTPException(404, "Message not found")
    # The session comes from the message, never from a caller-supplied value.
    await chat_history_service.assert_session_owner(msg["session_id"], user_id)

    if not message_markdown(msg):
        raise HTTPException(404, "Message has no content to export")

    # pandoc is a subprocess: off the event loop, or it stalls every other
    # request on this worker for the length of the conversion.
    docx_bytes = await asyncio.to_thread(build_message_docx, msg)

    return Response(
        content=docx_bytes,
        media_type=DOCX_CONTENT_TYPE,
        headers={"Content-Disposition": f'attachment; filename="{DOCX_FILENAME}"'},
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
