# app/routers/history/sessions.py
from fastapi import APIRouter, Depends, status

from core.dependencies import get_chat_history_service
from models.chat_history import (
    SessionCreateRequest,
    SessionEditRequest,
    SessionResponse,
)
from security.dependencies import get_current_user_id
from utils.user_management import handle_service_error, serialize_mongo_id

router = APIRouter(prefix="/sessions", tags=["Sessions"])

chat_history_service = get_chat_history_service()

# PATCH and DELETE carry no user_id anywhere in the path, query or body, so the
# router-level actor cross-check has nothing to compare and waves them through.
# These take the subject straight from the token instead.
CurrentUser = Depends(get_current_user_id)


@router.post("", status_code=status.HTTP_201_CREATED, response_model=SessionResponse)
@handle_service_error
async def create_session(request: SessionCreateRequest):
    """Create a new session. Session ID is auto-generated (client-provided ID is ignored)."""
    session = await chat_history_service.create_session(
        user_id=request.user_id,
        title=request.title,
        tags=request.tags,
        org_id=request.org_id,
    )
    return SessionResponse(**serialize_mongo_id(session))


@router.get("/{user_id}", response_model=list[SessionResponse])
@handle_service_error
async def list_sessions(
    user_id: str, org_id: str | None = None, limit: int = 10, skip: int = 0
):
    """List the caller's sessions. ``org_id`` omitted lists the personal profile.

    Membership is enforced inside get_sessions, so every caller is covered.
    """
    sessions = await chat_history_service.get_sessions(
        user_id=user_id, org_id=org_id, limit=limit, skip=skip
    )
    return [serialize_mongo_id(s) for s in sessions]


@router.get("/{user_id}/{session_id}", response_model=SessionResponse)
@handle_service_error
async def get_session(user_id: str, session_id: str):
    session = await chat_history_service.assert_session_access(session_id, user_id)
    return serialize_mongo_id(session)


@router.patch("/{session_id}", response_model=SessionResponse)
@handle_service_error
async def update_session(
    session_id: str, request: SessionEditRequest, user_id: str = CurrentUser
):
    """Rename or retag a session. Only its owner, and only while they still
    belong to the organization it was created under."""
    # assert_session_owner, not assert_session_access: the latter now admits any
    # member of an org-shared transcript, who must not be able to rename it.
    await chat_history_service.assert_session_owner(session_id, user_id)
    session = await chat_history_service.edit_session(
        session_id, title=request.title, tags=request.tags
    )
    return serialize_mongo_id(session)


@router.delete("/{session_id}", status_code=status.HTTP_204_NO_CONTENT)
@handle_service_error
async def delete_session(session_id: str, user_id: str = CurrentUser):
    """Soft-delete a session. Owner only — see update_session.

    The session is stamped archived, which hides the whole chat from every
    user-facing read. Its messages are deliberately retained — see
    ChatHistoryService.delete_session.
    """
    await chat_history_service.assert_session_owner(session_id, user_id)
    await chat_history_service.delete_session(session_id)
