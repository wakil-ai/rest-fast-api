import asyncio
from datetime import datetime, timezone
from typing import Any

from fastapi import HTTPException, status

from core.config import settings
from core.dependencies import get_chat_history_service, get_db_manager
from core.logger import logger
from models.projects import (
    ProjectCreateRequest,
    ProjectStatus,
    ProjectUpdateRequest,
)
from utils.user_management import clean_for_mongodb, generate_short_id

# A project row with `org_id` set is an enterprise Case, not a personal project.
# Rows created before Cases existed have no `org_id` key at all, so "personal"
# must match absent as well as null — the same shape as the shipped `project_id`
# filter on sessions.
PERSONAL_SCOPE: dict[str, Any] = {
    "$or": [{"org_id": {"$exists": False}}, {"org_id": None}]
}


def is_case(project: dict[str, Any]) -> bool:
    return bool(project.get("org_id"))


class ProjectService:
    """Mongo-backed legal projects: documents, sessions, and scoped file ingestion."""

    def __init__(self) -> None:
        self.db = get_db_manager()
        self.history = get_chat_history_service()
        self.prefix = "proj-"
        self.collection = settings.PROJECTS_COLLECTION

        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            loop = None
        if loop is not None:
            loop.create_task(self._init_collections())

    async def _init_collections(self) -> None:
        await self.db.create_collection(self.collection)

    def _member_service(self):
        from core.dependencies import get_project_member_service

        return get_project_member_service()

    async def _load_project_row(self, project_id: str) -> dict[str, Any]:
        rows = await self.db.find_documents(
            self.collection, {"_id": project_id}, limit=1
        )
        if not rows:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND, detail="Project not found"
            )
        return rows[0]

    async def get_membership_role(self, project_id: str, user_id: str) -> str | None:
        project = await self._load_project_row(project_id)
        if is_case(project):
            # Cases are reached through organization membership and leave
            # project_members empty, so the lookup below can only ever answer None —
            # reporting no role to a member who was just granted full access.
            return "owner" if project.get("owner_id") == user_id else "member"
        if project.get("owner_id") == user_id:
            return "owner"
        if await self._member_service().is_member(project_id, user_id):
            return "member"
        return None

    async def assert_project_access(
        self, project_id: str, user_id: str
    ) -> dict[str, Any]:
        project = await self._load_project_row(project_id)
        if is_case(project):
            # An archived Case 404s on the enterprise surface, so it must 404 here
            # too — otherwise its files, sessions and instructions stay fully live on
            # this route after an admin has archived it.
            if project.get("archived"):
                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND, detail="Project not found"
                )
            # An enterprise Case is reached through organization membership, not
            # through project_members, which stays unused for Cases.
            from core.dependencies import get_organization_service

            await get_organization_service().assert_org_member(
                project["org_id"], user_id
            )
            return project

        role = await self.get_membership_role(project_id, user_id)
        if role is None:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN, detail="Access denied"
            )
        return project

    @staticmethod
    def reject_if_case(project: dict[str, Any]) -> None:
        """Keep enterprise Cases out of the personal project surface.

        A Case's owner_id is an ordinary member, so an owner check alone would let
        them PATCH `status` straight to closed or archived through
        `/projects/{project_id}` — bypassing admin approval, the closure workflow
        and the activity log, all of which live in CaseService. 404 rather than 403
        because on this surface the row is not supposed to exist at all.
        """
        if is_case(project):
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND, detail="Project not found"
            )

    async def assert_project_owner(
        self, project_id: str, user_id: str
    ) -> dict[str, Any]:
        project = await self._load_project_row(project_id)
        self.reject_if_case(project)
        if project.get("owner_id") != user_id:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN, detail="Access denied"
            )
        return project

    async def get_project(self, project_id: str, user_id: str) -> dict[str, Any]:
        """Owner or member access."""
        return await self.assert_project_access(project_id, user_id)

    async def create_project(self, body: ProjectCreateRequest) -> dict[str, Any]:
        await self.history._ensure_user_exists(body.owner_id)

        project_id = generate_short_id(prefix=self.prefix, type="uuid7")
        now = datetime.now(timezone.utc)
        settings_dict = (
            body.settings.model_dump(exclude_none=True) if body.settings else {}
        )

        doc = clean_for_mongodb(
            {
                "_id": project_id,
                "owner_id": body.owner_id,
                "title": body.title,
                # No `org_id` key at all, deliberately. Writing an explicit null
                # would put every personal project in the deployment into the Case
                # partial indexes, which key on `org_id: {$exists: True}` precisely
                # to stay out of them. `is_case` and PERSONAL_SCOPE both already
                # read absence as personal, so the discriminator loses nothing.
                "files": [],
                "status": ProjectStatus.active.value,
                "stats": {"docs": 0, "chats": 0, "reminders": 0},
                "settings": settings_dict,
                "created_at": now,
                "updated_at": now,
            }
        )
        await self.db.insert_documents(self.collection, [doc])
        doc["membership_role"] = "owner"
        logger.info(f"Created project {project_id} for owner {body.owner_id}")
        return doc

    async def list_projects(
        self,
        user_id: str,
        *,
        status: ProjectStatus | None = None,
        limit: int = 50,
        skip: int = 0,
    ) -> list[dict[str, Any]]:
        await self.history._ensure_user_exists(user_id)
        member_ids = await self._member_service().list_member_project_ids(user_id)

        or_clauses: list[dict[str, Any]] = [{"owner_id": user_id}]
        if member_ids:
            or_clauses.append({"_id": {"$in": member_ids}})

        # Both predicates are `$or`, and a dict cannot hold two of those keys —
        # the second would silently replace the first and every org Case would
        # show up in this user's personal project list. They go under `$and`.
        query: dict[str, Any] = {"$and": [{"$or": or_clauses}, PERSONAL_SCOPE]}
        if status is not None:
            query["status"] = status.value

        rows = await self.db.find_documents(
            self.collection, query, limit=limit, skip=skip
        )
        out: list[dict[str, Any]] = []
        for row in rows:
            pid = row.get("_id")
            if row.get("owner_id") == user_id:
                role = "owner"
            else:
                role = "member"
            tagged = dict(row)
            tagged["membership_role"] = role
            out.append(tagged)
        return out

    async def update_project(
        self, project_id: str, body: ProjectUpdateRequest
    ) -> dict[str, Any]:
        project = await self.assert_project_owner(project_id, body.owner_id)
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
            project["membership_role"] = "owner"
            return project

        await self.db.update_documents(
            self.collection,
            {"_id": project_id},
            {"$set": clean_for_mongodb(updates)},
        )
        refreshed = await self.db.find_documents(
            self.collection, {"_id": project_id}, limit=1
        )
        doc = refreshed[0] if refreshed else project
        doc["membership_role"] = "owner"
        return doc

    async def append_file_id(self, project_id: str, file_id: str) -> None:
        await self.db.mongo_handler.db[self.collection].update_one(
            {"_id": project_id},
            {
                "$addToSet": {"files": file_id},
                "$set": {"updated_at": datetime.now(timezone.utc)},
            },
        )

    async def remove_file_id(self, project_id: str, file_id: str) -> None:
        await self.db.mongo_handler.db[self.collection].update_one(
            {"_id": project_id},
            {
                "$pull": {"files": file_id},
                "$set": {"updated_at": datetime.now(timezone.utc)},
            },
        )

    async def increment_stat(self, project_id: str, field: str, delta: int = 1) -> None:
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
        task_id: str | None = None,
    ) -> dict[str, Any]:
        project = await self.assert_project_access(project_id, user_id)
        # A session on a Case carries the Case's org_id. Without it the row is
        # personal-scope, and assert_session_access only re-checks membership when
        # org_id is set — so a member removed from the organization would keep read
        # and write on every Case conversation they had opened.
        org_id = project.get("org_id")

        if task_id:
            if not org_id:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail="task_id applies only to a Case",
                )
            from core.dependencies import get_task_service

            await get_task_service().assert_task_in_case(org_id, project_id, task_id)

        return await self.history.create_session(
            user_id=user_id,
            title=title,
            tags=tags,
            project_id=project_id,
            org_id=org_id,
            task_id=task_id,
        )

    async def list_project_sessions(
        self, project_id: str, user_id: str, limit: int = 50, skip: int = 0
    ) -> list[dict[str, Any]]:
        await self.assert_project_access(project_id, user_id)
        return await self.history.get_sessions_by_project(
            project_id=project_id, limit=limit, skip=skip
        )

    async def list_project_files(
        self, project_id: str, user_id: str, limit: int = 100
    ) -> list[dict[str, Any]]:
        await self.assert_project_access(project_id, user_id)
        return await self.history.get_files_by_project(project_id, limit=limit)

    @staticmethod
    def extract_instructions(project: dict[str, Any]) -> str | None:
        settings_dict = project.get("settings") or {}
        raw = settings_dict.get("instructions")
        if not isinstance(raw, str):
            return None
        text = raw.strip()
        return text or None

    async def get_project_instructions(
        self, project_id: str, user_id: str
    ) -> dict[str, Any]:
        project = await self.assert_project_access(project_id, user_id)
        return {
            "project_id": project_id,
            "instructions": self.extract_instructions(project),
            "updated_at": project.get("updated_at"),
        }

    async def set_project_instructions(
        self,
        project_id: str,
        user_id: str,
        instructions: str,
        *,
        create_only: bool = False,
    ) -> dict[str, Any]:
        project = await self.assert_project_access(project_id, user_id)
        existing = self.extract_instructions(project)
        if create_only and existing:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="Project instructions already exist; use PUT to replace them",
            )

        prev_settings = project.get("settings") or {}
        updates = {
            "settings": {
                **prev_settings,
                "instructions": instructions.strip(),
            },
            "updated_at": datetime.now(timezone.utc),
        }
        await self.db.update_documents(
            self.collection,
            {"_id": project_id},
            {"$set": clean_for_mongodb(updates)},
        )
        refreshed = await self.db.find_documents(
            self.collection, {"_id": project_id}, limit=1
        )
        doc = refreshed[0] if refreshed else project
        return {
            "project_id": project_id,
            "instructions": self.extract_instructions(doc),
            "updated_at": doc.get("updated_at"),
        }
