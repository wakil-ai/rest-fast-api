# app/routers/history/sessions.py
from fastapi import APIRouter, HTTPException, status

from app.core.dependencies import get_chat_history_service
from app.models.chat_history import (
    SessionCreateRequest,
    SessionCreateResponse,
    SessionResponse,
)
from app.utils.user_management import handle_service_error, serialize_mongo_id

router = APIRouter(prefix="/sessions", tags=["Sessions"])

chat_history_service = get_chat_history_service()


@router.post(
    "", status_code=status.HTTP_201_CREATED, response_model=SessionCreateResponse
)
@handle_service_error
async def create_session(request: SessionCreateRequest):
    session = await chat_history_service.create_session(
        user_id=request.user_id,
        session_id=request.session_id,
        project_id=request.project_id,
        title=request.title,
        tags=request.tags,
    )
    return {"info": serialize_mongo_id(session), "message": "Session created"}


@router.get("/{user_id}", response_model=list[SessionResponse])
@handle_service_error
async def list_sessions(user_id: str, project_id: str | None = None, limit: int = 50):
    sessions = await chat_history_service.get_sessions(
        user_id=user_id, project_id=project_id, limit=limit
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
async def update_session(
    session_id: str, title: str | None = None, tags: list[str] | None = None
):
    session = await chat_history_service.edit_session(session_id, title, tags)
    return serialize_mongo_id(session)


@router.delete("/{session_id}", status_code=status.HTTP_204_NO_CONTENT)
@handle_service_error
async def delete_session(session_id: str):
    await chat_history_service.delete_session(session_id)
