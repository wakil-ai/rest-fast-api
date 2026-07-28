"""Organizations: creation and the caller's organization list."""

import asyncio
from datetime import datetime, timezone
from typing import Any

from fastapi import HTTPException, status

from core.config import settings
from core.dependencies import get_db_manager
from core.logger import logger
from models.organizations import (
    OrganizationMembershipRole,
    OrganizationMemberStatus,
    OrganizationStatus,
)
from utils.user_management import clean_for_mongodb, generate_short_id

# Reads exclude soft-deleted docs, mirroring chat_history_service._ACTIVE_ONLY.
_ACTIVE_ONLY: dict[str, Any] = {"archived": {"$ne": True}}


class OrganizationService:
    """Organization lifecycle plus the admin/seat guards shared with membership."""

    def __init__(self) -> None:
        self.db = get_db_manager()
        self.orgs_collection = settings.ORGANIZATIONS_COLLECTION
        self.members_collection = settings.ORGANIZATION_MEMBERS_COLLECTION
        self.org_prefix = "org-"
        self.member_prefix = "omem-"

        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            loop = None
        if loop is not None:
            loop.create_task(self._init_collections())

    async def _init_collections(self) -> None:
        await self.db.create_collection(self.orgs_collection)
        await self.db.create_collection(self.members_collection)
        members = self.db.mongo_handler.db[self.members_collection]
        await members.create_index(
            [("org_id", 1), ("user_id", 1)], unique=True, name="org_user_unique"
        )
        await members.create_index(
            [("user_id", 1), ("joined_at", -1)], name="user_orgs"
        )

    async def load_org_row(self, org_id: str) -> dict[str, Any]:
        rows = await self.db.find_documents(
            self.orgs_collection, {"_id": org_id, **_ACTIVE_ONLY}, limit=1
        )
        if not rows:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND, detail="Organization not found"
            )
        return rows[0]

    async def get_membership(self, org_id: str, user_id: str) -> dict[str, Any] | None:
        rows = await self.db.find_documents(
            self.members_collection,
            {
                "org_id": org_id,
                "user_id": user_id,
                "status": OrganizationMemberStatus.active.value,
            },
            limit=1,
        )
        return rows[0] if rows else None

    async def active_member_count(self, org_id: str) -> int:
        rows = await self.db.find_documents(
            self.members_collection,
            {"org_id": org_id, "status": OrganizationMemberStatus.active.value},
            limit=settings.ORG_SEAT_LIMIT + 1,
        )
        return len(rows)

    async def assert_org_member(self, org_id: str, user_id: str) -> dict[str, Any]:
        org = await self.load_org_row(org_id)
        if not await self.get_membership(org_id, user_id):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="You are not a member of this organization",
            )
        return org

    async def assert_org_admin(self, org_id: str, user_id: str) -> dict[str, Any]:
        org = await self.load_org_row(org_id)
        membership = await self.get_membership(org_id, user_id)
        if not membership or membership.get("role") != (
            OrganizationMembershipRole.admin.value
        ):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Only an organization admin can perform this action",
            )
        return org

    async def create_organization(
        self,
        *,
        user_id: str,
        name: str,
        icon: str | None = None,
        settings_obj: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        clean_name = (name or "").strip()
        if not clean_name:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Organization name is required",
            )

        now = datetime.now(timezone.utc)
        org_id = generate_short_id(prefix=self.org_prefix, type="uuid7")
        org_doc = clean_for_mongodb(
            {
                "_id": org_id,
                "name": clean_name,
                "icon": icon,
                "created_by": user_id,
                "status": OrganizationStatus.active.value,
                "seat_limit": settings.ORG_SEAT_LIMIT,
                "settings": settings_obj or {},
                # ponytail: no endpoint flips this yet — there is no delete-org flow
                # in WK-204. Reads already filter on it so adding one is a one-liner.
                "archived": False,
                "created_at": now,
                "updated_at": now,
            }
        )
        await self.db.insert_documents(self.orgs_collection, [org_doc])

        # the user->org relationship is created AFTER the org, as a membership row.
        # Unlike projects, the admin is a real member row here.
        member_doc = clean_for_mongodb(
            {
                "_id": generate_short_id(prefix=self.member_prefix, type="uuid7"),
                "org_id": org_id,
                "user_id": user_id,
                "role": OrganizationMembershipRole.admin.value,
                "status": OrganizationMemberStatus.active.value,
                "joined_at": now,
            }
        )
        await self.db.insert_documents(self.members_collection, [member_doc])

        logger.info(f"Created organization {org_id} for user {user_id}")
        out = dict(org_doc)
        out["role"] = OrganizationMembershipRole.admin.value
        out["member_count"] = 1
        return out

    async def list_user_organizations(self, user_id: str) -> list[dict[str, Any]]:
        """Every active organization the user belongs to, with their role.

        Powers the frontend workspace switcher: a user may be admin of one org and
        a member of another, plus their personal (non-org) profile.
        """
        memberships = await self.db.find_documents(
            self.members_collection,
            {"user_id": user_id, "status": OrganizationMemberStatus.active.value},
            limit=100,
        )
        if not memberships:
            return []

        role_by_org = {
            m["org_id"]: m.get("role") for m in memberships if m.get("org_id")
        }
        orgs = await self.db.find_documents(
            self.orgs_collection,
            {"_id": {"$in": list(role_by_org)}, **_ACTIVE_ONLY},
            limit=100,
        )

        out: list[dict[str, Any]] = []
        for org in orgs:
            row = dict(org)
            row["role"] = role_by_org.get(org["_id"])
            row["member_count"] = await self.active_member_count(org["_id"])
            out.append(row)
        return out
