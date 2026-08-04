"""Organizations: creation and the caller's organization list."""

import asyncio
from datetime import datetime, timezone
from typing import Any

from fastapi import HTTPException, status

from core.config import settings
from core.dependencies import get_db_manager, get_storage_service
from core.logger import logger
from models.organizations import (
    OrganizationMembershipRole,
    OrganizationMemberStatus,
    OrganizationStatus,
)
from utils.user_management import clean_for_mongodb, generate_short_id

# Reads exclude soft-deleted docs, mirroring chat_history_service._ACTIVE_ONLY.
_ACTIVE_ONLY: dict[str, Any] = {"archived": {"$ne": True}}

# Browser-renderable raster formats only. SVG is deliberately absent: it can carry
# script, and these objects are served from a world-readable bucket.
AVATAR_CONTENT_TYPES = frozenset({"image/png", "image/jpeg", "image/webp"})
MAX_AVATAR_BYTES = 2 * 1024 * 1024


def _avatar_path(org_id: str) -> str:
    """One object per organization, overwritten in place by every upload."""
    return f"organizations/{org_id}/avatar"


def _public_bucket() -> str:
    """The world-readable bucket avatars live in, or a loud failure.

    Never the private document bucket, whether it was left unset or pointed at the
    same name: an object written there is unreachable through the public URL handed
    to the browser, so the upload would report success and the picture would render
    as broken with nothing to explain it.
    """
    bucket = (settings.GCS_PUBLIC_BUCKET_NAME or "").strip()
    if not bucket:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=(
                "Organization avatars are unavailable: GCS_PUBLIC_BUCKET_NAME is "
                "not configured."
            ),
        )
    if bucket == (settings.GCS_BUCKET_NAME or "").strip():
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=(
                "Organization avatars are unavailable: GCS_PUBLIC_BUCKET_NAME must "
                "name a different bucket from GCS_BUCKET_NAME."
            ),
        )
    return bucket


def assert_avatar_size(size: int | None) -> None:
    """Reject an oversized image, by declared size before it is read or real length after.

    The route calls this with ``UploadFile.size`` so a multi-gigabyte body is refused
    before being materialized in memory — which happens ahead of the admin check,
    since the bytes are a route argument. A declared size is client-supplied, so the
    service still checks the length it actually received.
    """
    if size is not None and size > MAX_AVATAR_BYTES:
        raise HTTPException(
            status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            detail=f"Image exceeds {MAX_AVATAR_BYTES // (1024 * 1024)} MB",
        )


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
                # Set only by set_organization_avatar; there is no client-supplied URL.
                "avatar_url": None,
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

        # Workflow states are deliberately NOT seeded here. list_states seeds any
        # board that reads back empty, which covers organizations created before
        # that feature existed — so seeding on creation too would be a second path
        # to the same outcome, and would couple org creation to a collaborator it
        # otherwise has no reason to hold.
        logger.info(f"Created organization {org_id} for user {user_id}")
        out = dict(org_doc)
        out["role"] = OrganizationMembershipRole.admin.value
        out["member_count"] = 1
        return out

    async def update_organization(
        self, *, org_id: str, user_id: str, updates: dict[str, Any]
    ) -> dict[str, Any]:
        """Admin-only edit of an organization's own fields.

        Every key is unpacked by hand rather than passed to ``$set`` as a block:
        the body model already limits the shape, but building the update explicitly
        is what keeps status, seat_limit and archived out of reach for good.
        """
        org = await self.assert_org_admin(org_id, user_id)

        set_fields: dict[str, Any] = {}
        if "name" in updates:
            clean_name = (updates["name"] or "").strip()
            if not clean_name:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail="Organization name is required",
                )
            set_fields["name"] = clean_name
        if "settings" in updates:
            set_fields["settings"] = updates["settings"] or {}

        return await self._apply_admin_update(org, set_fields)

    async def set_organization_avatar(
        self, *, org_id: str, user_id: str, data: bytes, content_type: str | None
    ) -> dict[str, Any]:
        """Store an organization's picture and record its permanent public URL.

        One fixed object per organization, overwritten on every upload, so there is
        never a second blob to garbage-collect. Because the path never changes, the
        stored URL carries a ``?v=`` stamp — without it browsers would keep serving
        the previous picture from cache after a change. GCS ignores the parameter.
        """
        org = await self.assert_org_admin(org_id, user_id)

        media_type = (content_type or "").split(";")[0].strip().lower()
        if media_type not in AVATAR_CONTENT_TYPES:
            raise HTTPException(
                status_code=status.HTTP_415_UNSUPPORTED_MEDIA_TYPE,
                detail=(
                    "Unsupported image type. Allowed: "
                    + ", ".join(sorted(AVATAR_CONTENT_TYPES))
                ),
            )
        if not data:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST, detail="Uploaded file is empty"
            )
        assert_avatar_size(len(data))

        public_url = get_storage_service().upload_file(
            data=data,
            destination_path=_avatar_path(org_id),
            content_type=media_type,
            return_signed_url=False,
            bucket_name=_public_bucket(),
        )
        stamp = int(datetime.now(timezone.utc).timestamp())
        return await self._apply_admin_update(
            org, {"avatar_url": f"{public_url}?v={stamp}"}
        )

    async def clear_organization_avatar(
        self, *, org_id: str, user_id: str
    ) -> dict[str, Any]:
        """Remove an organization's picture. Admin only.

        The blob is deleted rather than just unlinked: it sits in a world-readable
        bucket, so anyone who saw the URL earlier would otherwise keep it. A storage
        failure is logged but does not block the removal — leaving an admin unable to
        drop a picture because GCS is having a moment is the worse outcome.
        """
        org = await self.assert_org_admin(org_id, user_id)

        if org.get("avatar_url"):
            deleted = get_storage_service().permanently_delete_file(
                _avatar_path(org_id), bucket_name=_public_bucket()
            )
            if not deleted:
                logger.warning(
                    f"Avatar object for {org_id} outlived its document; "
                    "the public URL stays reachable until it is removed by hand"
                )

        return await self._apply_admin_update(org, {"avatar_url": None})

    async def _apply_admin_update(
        self, org: dict[str, Any], set_fields: dict[str, Any]
    ) -> dict[str, Any]:
        """Persist an already-authorized ``$set`` and shape the response row.

        Callers must have passed assert_org_admin, which is also why the role is
        filled in from that fact rather than read back.
        """
        org_id = org["_id"]
        row = dict(org)
        if set_fields:
            set_fields = {**set_fields, "updated_at": datetime.now(timezone.utc)}
            await self.db.update_documents(
                self.orgs_collection,
                {"_id": org_id, **_ACTIVE_ONLY},
                {"$set": clean_for_mongodb(set_fields)},
            )
            row.update(set_fields)

        row["role"] = OrganizationMembershipRole.admin.value
        row["member_count"] = await self.active_member_count(org_id)
        return row

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
