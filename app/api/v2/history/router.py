# app/routers/history/router.py
from fastapi import APIRouter

from app.api.v2.history.feedbacks import router as feedbacks_router
from app.api.v2.history.files import router as files_router
from app.api.v2.history.messages import router as messages_router
from app.api.v2.history.sessions import router as sessions_router
from app.api.v2.history.users import router as users_router

router = APIRouter(prefix="/history", tags=["Chat History"])

router.include_router(users_router)
router.include_router(sessions_router)
router.include_router(messages_router)
router.include_router(feedbacks_router)
router.include_router(files_router)