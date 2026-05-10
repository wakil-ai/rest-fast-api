from datetime import datetime, timezone
from typing import Any
import asyncio

from fastapi import HTTPException, status

from app.core.config import settings
from app.core.dependencies import get_chat_history_service, get_db_manager
from app.core.logger import logger
from app.models.projects import (
    ProjectCreateRequest,
    ProjectStatus,
    ProjectUpdateRequest,
)
from app.utils.user_management import clean_for_mongodb, generate_short_id


class ProjectService:
    """Mongo-backed legal projects: documents, sessions, and scoped file ingestion."""

    def __init__(self) -> None:
        self.db = get_db_manager()
        self.history = get_chat_history_service()
        self.prefix = "proj-"
        self.collection = settings.PROJECTS_COLLECTION

        asyncio.create_task(self._init_collections())

    async def _init_collections(self) -> None:
        await self.db.create_collection(self.collection)

    async def create_project(self, body: ProjectCreateRequest) -> dict[str, Any]:
        await self.history._ensure_user_exists(body.owner_id)

        project_id = generate_short_id(prefix=self.prefix, type="uuid7")
        now = datetime.now(timezone.utc)
        settings_dict = (body.settings.model_dump(exclude_none=True) if body.settings else {})

        doc = clean_for_mongodb(
            {
                "_id": project_id,
                "owner_id": body.owner_id,
                "title": body.title,
                "files": [],
                "status": ProjectStatus.active.value,
                "stats": {"docs": 0, "chats": 0, "reminders": 0},
                "settings": settings_dict,
                "created_at": now,
                "updated_at": now,
            }
        )
        await self.db.insert_documents(self.collection, [doc])
        logger.info(f"Created project {project_id} for owner {body.owner_id}")
        return doc

    async def list_projects(
        self,
        owner_id: str,
        *,
        status: ProjectStatus | None = None,
        limit: int = 50,
        skip: int = 0,
    ) -> list[dict[str, Any]]:
        await self.history._ensure_user_exists(owner_id)
        query: dict[str, Any] = {"owner_id": owner_id}

        if status is not None:
            query["status"] = status.value

        return await self.db.find_documents(
            self.collection, query, limit=limit, skip=skip
        )

    async def get_project(self, project_id: str, owner_id: str) -> dict[str, Any]:
        rows = await self.db.find_documents(self.collection, {"_id": project_id}, limit=1)
        if not rows:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND, detail="Project not found"
            )
        project = rows[0]
        if project.get("owner_id") != owner_id:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN, detail="Access denied"
            )
        return project

    async def update_project(
        self, project_id: str, body: ProjectUpdateRequest
    ) -> dict[str, Any]:
        project = await self.get_project(project_id, body.owner_id)
        updates: dict[str, Any] = {"updated_at": datetime.now(timezone.utc)}

        if body.title is not None:
            updates["title"] = body.title
        if body.status is not None:
            updates["status"] = body.status.value
        if body.settings is not None:
            prev = project.get("settings") or {}
            patch = body.settings.model_dump(exclude_none=True)
            updates["settings"] = {**prev, **patch}

        if len(updates) <= 1:
            return project

        await self.db.update_documents(
            self.collection,
            {"_id": project_id},
            {"$set": clean_for_mongodb(updates)},
        )
        refreshed = await self.db.find_documents(
            self.collection, {"_id": project_id}, limit=1
        )
        return refreshed[0] if refreshed else project

    async def append_file_id(self, project_id: str, file_id: str) -> None:
        await self.db.mongo_handler.db[self.collection].update_one(
            {"_id": project_id},
            {
                "$addToSet": {"files": file_id},
                "$set": {"updated_at": datetime.now(timezone.utc)},
            },
        )

    async def increment_stat(
        self, project_id: str, field: str, delta: int = 1
    ) -> None:
        if field not in ("docs", "chats", "reminders"):
            return
        key = f"stats.{field}"
        await self.db.mongo_handler.db[self.collection].update_one(
            {"_id": project_id},
            {"$inc": {key: delta}, "$set": {"updated_at": datetime.now(timezone.utc)}},
        )

    async def create_session_for_project(
        self,
        project_id: str,
        user_id: str,
        title: str | None,
        tags: list[str] | None,
    ) -> dict[str, Any]:
        await self.get_project(project_id, user_id)
        return await self.history.create_session(
            user_id=user_id,
            title=title,
            tags=tags,
            project_id=project_id,
        )

    async def list_project_sessions(
        self, project_id: str, user_id: str, limit: int = 50, skip: int = 0
    ) -> list[dict[str, Any]]:
        await self.get_project(project_id, user_id)
        return await self.history.get_sessions_by_project(
            project_id=project_id, limit=limit, skip=skip
        )

    async def list_project_files(
        self, project_id: str, user_id: str, limit: int = 100
    ) -> list[dict[str, Any]]:
        await self.get_project(project_id, user_id)
        return await self.history.get_files_by_project(project_id, limit=limit)