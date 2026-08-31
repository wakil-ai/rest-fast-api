# app/routers/history/sessions.py
from fastapi import APIRouter, Depends, HTTPException, status

from core.dependencies import get_chat_history_service
from security.dependencies import get_current_user_id
from models.chat_history import (
    SessionCreateRequest,
    SessionEditRequest,
    SessionResponse,
)
from utils.user_management import handle_service_error, serialize_mongo_id

router = APIRouter(prefix="/sessions", tags=["Sessions"])

chat_history_service = get_chat_history_service()


@router.post("", status_code=status.HTTP_201_CREATED, response_model=SessionResponse)
@handle_service_error
async def create_session(request: SessionCreateRequest):
    """Create a new session. Session ID is auto-generated (client-provided ID is ignored)."""
    session = await chat_history_service.create_session(
        user_id=request.user_id,
        title=request.title,
        tags=request.tags,
    )
    return SessionResponse(**serialize_mongo_id(session))


@router.get("/{user_id}", response_model=list[SessionResponse])
@handle_service_error
async def list_sessions(user_id: str, limit: int = 10, skip: int = 0):
    sessions = await chat_history_service.get_sessions(
        user_id=user_id, limit=limit, skip=skip
    )
    return [serialize_mongo_id(s) for s in sessions]


@router.get("/{user_id}/{session_id}", response_model=SessionResponse)
@handle_service_error
async def get_session(user_id: str, session_id: str):
    session = await chat_history_service.get_session(session_id)
    if not session:
        raise HTTPException(404, "Session not found")
    if session["user_id"] != user_id:
        raise HTTPException(403, "Access denied")
    return serialize_mongo_id(session)


@router.patch("/{session_id}", response_model=SessionResponse)
@handle_service_error
async def update_session(session_id: str, request: SessionEditRequest):
    session = await chat_history_service.edit_session(
        session_id, title=request.title, tags=request.tags
    )
    return serialize_mongo_id(session)


@router.delete("/{session_id}", status_code=status.HTTP_204_NO_CONTENT)
@handle_service_error
async def delete_session(
    session_id: str, user_id: str = Depends(get_current_user_id)
):
    """Soft-delete a session. Owner only.

    This route carries no user_id in the path, query or body, so the
    router-level actor cross-check has nothing to compare and waves it
    through — the ownership assert below is the only guard it gets.

    The session is stamped archived, which hides the whole chat from every
    user-facing read. Its messages are deliberately retained — see
    ChatHistoryService.delete_session.
    """
    await chat_history_service.assert_session_owner(session_id, user_id)
    await chat_history_service.delete_session(session_id)
