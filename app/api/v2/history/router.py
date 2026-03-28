# app/routers/history/router.py
from fastapi import APIRouter

from app.api.v2.history.feedbacks import router as feedbacks_router
from app.api.v2.history.files import router as files_router
from app.api.v2.history.messages import router as messages_router
from app.api.v2.history.sessions import router as sessions_router
from app.api.v2.history.users import router as users_router
from app.core.dependencies import get_chat_history_service
from app.utils.user_management import (
    handle_service_error,
    sanitize_message_for_response,
    serialize_mongo_id,
)

router = APIRouter(prefix="/history", tags=["Chat History"])
chat_history_service = get_chat_history_service()

router.include_router(users_router)
router.include_router(sessions_router)
router.include_router(messages_router)
router.include_router(feedbacks_router)
router.include_router(files_router)


@router.get("/sync/{user_id}")
@handle_service_error
async def sync_user_data(user_id: str, months: int = 3):
    data = await chat_history_service.get_sync_data(user_id=user_id, months=months)

    data["sessions"] = [serialize_mongo_id(s) for s in data["sessions"]]

    serialized_messages = {
        sid: [sanitize_message_for_response(serialize_mongo_id(m)) for m in msgs]
        for sid, msgs in data["messages"].items()
    }
    data["messages"] = serialized_messages

    return data
