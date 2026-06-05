# src/api/v2/history/router.py
from fastapi import APIRouter

from src.api.v2.history.feedbacks import router as feedbacks_router
from src.api.v2.history.files import router as files_router
from src.api.v2.history.messages import router as messages_router
from src.api.v2.history.project_collaboration import (
    router as project_collaboration_router,
)
from src.api.v2.history.projects import router as projects_router
from src.api.v2.history.sessions import router as sessions_router
from src.api.v2.history.users import router as users_router

router = APIRouter(prefix="/history", tags=["Chat History"])

router.include_router(users_router)
router.include_router(sessions_router)
router.include_router(messages_router)
router.include_router(feedbacks_router)
router.include_router(files_router)
router.include_router(project_collaboration_router)
router.include_router(projects_router)
