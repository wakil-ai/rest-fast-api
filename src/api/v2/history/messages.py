# app/routers/history/messages.py
import asyncio

from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.responses import Response

from core.dependencies import get_chat_history_service
from models.chat_history import (
    MessageCreateRequest,
    MessageResponse,
    MessageSharedResponse,
)
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


async def current_actor(request: Request) -> str | None:
    """The bearer subject, or None when the caller used a service API key.

    verify_user_or_service_auth records the JWT subject on request.state
    (security/dependencies.py:174) and records nothing for an API key (:178-180).
    Depends(get_current_user_id) would 401 the latter, and DT reads sessions it
    does not own with only x-dt-team-api-key (docs/dt-team-integration.md:210).
    """
    return getattr(request.state, "authenticated_user_id", None)


CurrentActor = Depends(current_actor)


async def _assert_access(session_id: str, actor: str | None) -> None:
    """Ownership applies to users, not to trusted backends — see current_actor.

    These handlers carry no user_id in the path, query or body, so the router-level
    actor cross-check (security/dependencies.py:183) has nothing to compare and
    waves them through. This is the only guard they get.
    """
    if actor:
        await chat_history_service.assert_session_access(session_id, actor)


@router.post("", status_code=status.HTTP_201_CREATED, response_model=MessageResponse)
@handle_service_error
async def create_message(
    request: MessageCreateRequest, actor: str | None = CurrentActor
):
    """Create a new message. Message ID is auto-generated (client-provided ID is ignored)."""
    # Before the write, so a refused request never reaches add_message.
    await _assert_access(request.session_id, actor)
    msg = await chat_history_service.add_message(
        session_id=request.session_id,
        file_ids=request.file_ids,
        content=request.content,
        metadata=request.metadata,
    )
    return MessageResponse(**sanitize_message_for_response(serialize_mongo_id(msg)))


@router.get("/{session_id}", response_model=list[MessageResponse])
@handle_service_error
async def list_messages(
    session_id: str, limit: int = 50, actor: str | None = CurrentActor
):
    await _assert_access(session_id, actor)
    msgs = await chat_history_service.get_messages(session_id, limit)
    return [sanitize_message_for_response(serialize_mongo_id(m)) for m in msgs]


@router.get("/{message_id}/docx")
@handle_service_error
async def download_message_docx(message_id: str, actor: str | None = CurrentActor):
    """Render an assistant reply as a .docx for download.

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
    await _assert_access(msg["session_id"], actor)

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
async def get_message(
    session_id: str, message_id: str, actor: str | None = CurrentActor
):
    await _assert_access(session_id, actor)
    msg = await chat_history_service.get_message(message_id)
    if not msg:
        raise HTTPException(404, "Message not found")
    # Belt to the braces above: the session is the caller's, but this confirms the
    # message is that session's rather than one borrowed from elsewhere.
    if msg["session_id"] != session_id:
        raise HTTPException(403, "Message does not belong to this session")
    return sanitize_message_for_response(serialize_mongo_id(msg))


@router.post("/{message_id}/share", response_model=MessageSharedResponse)
@handle_service_error
async def share_message(message_id: str, actor: str | None = CurrentActor):
    """Mint a share link for a message. Only for a message the caller can read.

    The share is served by GET /api/v2/share/{share_id} with no authentication, so
    this is the one endpoint here that turns a private message into a public URL —
    the session check is what stops that being anyone's message.
    """
    msg = await chat_history_service.get_message(message_id)
    if not msg:
        raise HTTPException(404, "Message not found")
    # The session comes from the message, never from a caller-supplied value, so
    # there is nothing here to point at a session the caller does have access to.
    await _assert_access(msg["session_id"], actor)
    # A service-key caller records shared_by: None. The removed request body used
    # to let one name an arbitrary user, which no integration does and which no
    # bearer caller could ever do (verify_authenticated_actor pinned it to the
    # token). Attribution is deliberately unavailable to callers without identity.
    share = await chat_history_service.share_message(
        message_id=message_id, user_id=actor
    )
    return serialize_mongo_id(share)
