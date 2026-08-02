import asyncio
from datetime import datetime, timedelta, timezone
from typing import Any

from fastapi import HTTPException, status

from core.config import settings
from core.dependencies import get_chat_history_service, get_db_manager
from core.logger import logger
from models.project_collaboration import ProjectInviteStatus, ProjectMembershipRole
from models.projects import ProjectStatus
from utils.user_management import clean_for_mongodb, generate_short_id


class ProjectMemberService:
    """Project members and invitation links."""

    def __init__(self) -> None:
        self.db = get_db_manager()
        self.history = get_chat_history_service()
        self.members_collection = settings.PROJECT_MEMBERS_COLLECTION
        self.invites_collection = settings.PROJECT_INVITES_COLLECTION
        self.invite_prefix = "pinv-"
        self.member_prefix = "pmem-"

        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            loop = None
        if loop is not None:
            loop.create_task(self._init_collections())

    async def _init_collections(self) -> None:
        await self.db.create_collection(self.members_collection)
        await self.db.create_collection(self.invites_collection)
        members = self.db.mongo_handler.db[self.members_collection]
        invites = self.db.mongo_handler.db[self.invites_collection]
        await members.create_index(
            [("project_id", 1), ("user_id", 1)], unique=True, name="project_user_unique"
        )
        await members.create_index(
            [("user_id", 1), ("joined_at", -1)], name="user_projects"
        )
        await invites.create_index(
            [("project_id", 1), ("status", 1)], name="project_invites"
        )

    async def is_member(self, project_id: str, user_id: str) -> bool:
        rows = await self.db.find_documents(
            self.members_collection,
            {"project_id": project_id, "user_id": user_id},
            limit=1,
        )
        return bool(rows)

    async def list_member_project_ids(self, user_id: str) -> list[str]:
        rows = await self.db.find_documents(
            self.members_collection,
            {"user_id": user_id},
            limit=500,
        )
        return [r["project_id"] for r in rows if r.get("project_id")]

    async def add_member(
        self,
        *,
        project_id: str,
        user_id: str,
        invited_by: str,
        invite_id: str | None = None,
    ) -> dict[str, Any]:
        if await self.is_member(project_id, user_id):
            rows = await self.db.find_documents(
                self.members_collection,
                {"project_id": project_id, "user_id": user_id},
                limit=1,
            )
            return rows[0]

        now = datetime.now(timezone.utc)
        doc = clean_for_mongodb(
            {
                "_id": generate_short_id(prefix=self.member_prefix, type="uuid7"),
                "project_id": project_id,
                "user_id": user_id,
                "role": ProjectMembershipRole.member.value,
                "joined_at": now,
                "invited_by": invited_by,
                "invite_id": invite_id,
            }
        )
        await self.db.insert_documents(self.members_collection, [doc])
        logger.info(f"Added member {user_id} to project {project_id}")
        return doc

    async def remove_member(
        self, project_id: str, member_user_id: str, owner_id: str
    ) -> None:
        from services.project_service import ProjectService

        await ProjectService().assert_project_owner(project_id, owner_id)
        if member_user_id == owner_id:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Cannot remove the project owner",
            )
        result = await self.db.mongo_handler.db[self.members_collection].delete_one(
            {"project_id": project_id, "user_id": member_user_id}
        )
        if result.deleted_count == 0:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND, detail="Member not found"
            )

    async def list_members(
        self, project_id: str, project_doc: dict[str, Any]
    ) -> list[dict[str, Any]]:
        owner_id = project_doc["owner_id"]
        owner_user = await self.history.get_user(owner_id)
        members_out: list[dict[str, Any]] = [
            {
                "user_id": owner_id,
                "role": ProjectMembershipRole.owner.value,
                "joined_at": project_doc.get("created_at"),
                "first_name": (owner_user or {}).get("first_name"),
                "last_name": (owner_user or {}).get("last_name"),
                "username": (owner_user or {}).get("username"),
                "picture": (owner_user or {}).get("picture"),
            }
        ]

        rows = await self.db.find_documents(
            self.members_collection, {"project_id": project_id}, limit=200
        )
        for row in rows:
            uid = row.get("user_id")
            user = await self.history.get_user(uid) if uid else None
            members_out.append(
                {
                    "user_id": uid,
                    "role": ProjectMembershipRole.member.value,
                    "joined_at": row.get("joined_at"),
                    "first_name": (user or {}).get("first_name"),
                    "last_name": (user or {}).get("last_name"),
                    "username": (user or {}).get("username"),
                    "picture": (user or {}).get("picture"),
                }
            )
        return members_out

    def _invite_join_path(self, invite_id: str) -> str:
        return f"projects/join/{invite_id}"

    async def _load_invite_row(self, invite_id: str) -> dict[str, Any]:
        rows = await self.db.find_documents(
            self.invites_collection, {"_id": invite_id}, limit=1
        )
        if not rows:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND, detail="Invite not found"
            )
        return rows[0]

    def _effective_invite_status(self, invite: dict[str, Any]) -> str:
        raw = invite.get("status", ProjectInviteStatus.pending.value)
        if raw != ProjectInviteStatus.pending.value:
            return raw
        expires_at = invite.get("expires_at")
        if isinstance(expires_at, datetime):
            exp = (
                expires_at
                if expires_at.tzinfo
                else expires_at.replace(tzinfo=timezone.utc)
            )
            if exp < datetime.now(timezone.utc):
                return ProjectInviteStatus.expired.value
        return raw

    async def _assert_invite_owner(
        self, project_id: str, user_id: str
    ) -> dict[str, Any]:
        """Invite links can only be created or managed by the project owner."""
        from services.project_service import ProjectService

        svc = ProjectService()
        project = await svc._load_project_row(project_id)
        # Cases never use project_members — it is reserved for the per-case access
        # control customization. Without this a Case owner could mint an invite
        # link that anyone outside the organization accepts.
        svc.reject_if_case(project)
        if project.get("owner_id") != user_id:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Only the project owner can create or manage invite links",
            )
        return project

    async def create_invite(self, project_id: str, user_id: str) -> dict[str, Any]:
        project = await self._assert_invite_owner(project_id, user_id)
        if project.get("status") != ProjectStatus.active.value:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Invites can only be created for active projects",
            )

        invite_id = generate_short_id(prefix=self.invite_prefix, type="uuid7")
        now = datetime.now(timezone.utc)
        expires_at = now + timedelta(hours=settings.PROJECT_INVITE_TTL_HOURS)
        doc = clean_for_mongodb(
            {
                "_id": invite_id,
                "project_id": project_id,
                "created_by": user_id,
                "status": ProjectInviteStatus.pending.value,
                "expires_at": expires_at,
                "accepted_by": None,
                "accepted_at": None,
                "created_at": now,
            }
        )
        await self.db.insert_documents(self.invites_collection, [doc])
        doc["join_path"] = self._invite_join_path(invite_id)
        return doc

    async def list_pending_invites(
        self, project_id: str, user_id: str
    ) -> list[dict[str, Any]]:
        await self._assert_invite_owner(project_id, user_id)
        rows = await self.db.find_documents(
            self.invites_collection,
            {"project_id": project_id, "status": ProjectInviteStatus.pending.value},
            limit=100,
        )
        out = []
        for row in rows:
            eff = self._effective_invite_status(row)
            if eff == ProjectInviteStatus.expired.value:
                continue
            d = dict(row)
            d["join_path"] = self._invite_join_path(row["_id"])
            d["status"] = eff
            out.append(d)
        return out

    async def revoke_invite(
        self, project_id: str, invite_id: str, user_id: str
    ) -> None:
        await self._assert_invite_owner(project_id, user_id)
        invite = await self._load_invite_row(invite_id)
        if invite.get("project_id") != project_id:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND, detail="Invite not found"
            )
        if invite.get("status") != ProjectInviteStatus.pending.value:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Only pending invites can be revoked",
            )
        await self.db.update_documents(
            self.invites_collection,
            {"_id": invite_id},
            {"$set": clean_for_mongodb({"status": ProjectInviteStatus.revoked.value})},
        )

    async def get_invite_preview(self, invite_id: str, user_id: str) -> dict[str, Any]:
        from services.project_service import ProjectService

        invite = await self._load_invite_row(invite_id)
        eff_status = self._effective_invite_status(invite)
        project_id = invite["project_id"]
        project_rows = await self.db.find_documents(
            settings.PROJECTS_COLLECTION, {"_id": project_id}, limit=1
        )
        if not project_rows:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND, detail="Project not found"
            )
        project = project_rows[0]
        owner_id = project.get("owner_id", "")
        owner_user = await self.history.get_user(owner_id)
        owner_name = None
        if owner_user:
            parts = [
                owner_user.get("first_name") or "",
                owner_user.get("last_name") or "",
            ]
            owner_name = " ".join(p for p in parts if p).strip() or owner_user.get(
                "username"
            )

        already_member = False
        is_owner = user_id == owner_id
        if is_owner:
            already_member = True
        elif await self.is_member(project_id, user_id):
            already_member = True

        return {
            "invite_id": invite_id,
            "project_id": project_id,
            "project_title": project.get("title", ""),
            "project_status": project.get("status", ProjectStatus.active.value),
            "owner_id": owner_id,
            "owner_display_name": owner_name,
            "status": eff_status,
            "expires_at": invite.get("expires_at"),
            "already_member": already_member,
            "is_owner": is_owner,
        }

    async def accept_invite(self, invite_id: str, user_id: str) -> dict[str, Any]:
        await self.history._ensure_user_exists(user_id)
        invitee = await self.history.get_user(user_id)
        if invitee and invitee.get("is_blocked"):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="User account is blocked",
            )

        invite = await self._load_invite_row(invite_id)
        eff_status = self._effective_invite_status(invite)
        if eff_status == ProjectInviteStatus.expired.value:
            raise HTTPException(
                status_code=status.HTTP_410_GONE, detail="Invite has expired"
            )
        if eff_status == ProjectInviteStatus.revoked.value:
            raise HTTPException(
                status_code=status.HTTP_410_GONE, detail="Invite has been revoked"
            )
        if eff_status == ProjectInviteStatus.accepted.value:
            raise HTTPException(
                status_code=status.HTTP_410_GONE, detail="Invite has already been used"
            )
        if eff_status != ProjectInviteStatus.pending.value:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Invite is not available",
            )

        project_id = invite["project_id"]
        project_rows = await self.db.find_documents(
            settings.PROJECTS_COLLECTION, {"_id": project_id}, limit=1
        )
        if not project_rows:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND, detail="Project not found"
            )
        project = project_rows[0]
        owner_id = project.get("owner_id", "")

        if project.get("status") != ProjectStatus.active.value:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Project is not accepting new members",
            )

        if user_id == owner_id:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Project owner cannot accept an invite",
            )

        if await self.is_member(project_id, user_id):
            member_row = (
                await self.db.find_documents(
                    self.members_collection,
                    {"project_id": project_id, "user_id": user_id},
                    limit=1,
                )
            )[0]
            return {
                "project_id": project_id,
                "user_id": user_id,
                "membership_role": ProjectMembershipRole.member.value,
                "joined_at": member_row.get("joined_at") or datetime.now(timezone.utc),
            }

        owner_user = await self.history.get_user(owner_id)
        invitee_web = (invitee or {}).get("web_client")
        owner_web = (owner_user or {}).get("web_client")
        if invitee_web and owner_web and invitee_web != owner_web:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Cannot join a project from a different product tenant",
            )

        now = datetime.now(timezone.utc)
        member_doc = await self.add_member(
            project_id=project_id,
            user_id=user_id,
            invited_by=invite.get("created_by") or owner_id,
            invite_id=invite_id,
        )

        await self.db.update_documents(
            self.invites_collection,
            {"_id": invite_id, "status": ProjectInviteStatus.pending.value},
            {
                "$set": clean_for_mongodb(
                    {
                        "status": ProjectInviteStatus.accepted.value,
                        "accepted_by": user_id,
                        "accepted_at": now,
                    }
                )
            },
        )

        return {
            "project_id": project_id,
            "user_id": user_id,
            "membership_role": ProjectMembershipRole.member.value,
            "joined_at": member_doc.get("joined_at") or now,
        }
