"""Organization membership and one-time invitation links.

An invite is single-use: the first successful acceptance flips it to ``accepted``
and every later attempt is rejected with 410.
"""

import asyncio
from datetime import datetime, timedelta, timezone
from typing import Any

from fastapi import HTTPException, status

from core.config import settings
from core.dependencies import (
    get_chat_history_service,
    get_db_manager,
    get_organization_service,
    get_storage_service,
)
from core.logger import logger
from models.organizations import (
    OrganizationInviteStatus,
    OrganizationMembershipRole,
    OrganizationMemberStatus,
    OrganizationStatus,
)
from services.organization_service import avatar_blob_key
from utils.user_management import clean_for_mongodb, generate_short_id


class OrganizationMemberService:
    """Organization members and one-time invitation links."""

    def __init__(self) -> None:
        self.db = get_db_manager()
        self.history = get_chat_history_service()
        self.orgs = get_organization_service()
        self.members_collection = settings.ORGANIZATION_MEMBERS_COLLECTION
        self.invites_collection = settings.ORGANIZATION_INVITES_COLLECTION
        self.invite_prefix = "oinv-"
        self.member_prefix = "omem-"

        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            loop = None
        if loop is not None:
            loop.create_task(self._init_collections())

    async def _init_collections(self) -> None:
        await self.db.create_collection(self.invites_collection)
        invites = self.db.mongo_handler.db[self.invites_collection]
        await invites.create_index([("org_id", 1), ("status", 1)], name="org_invites")

    def invite_join_path(self, invite_id: str) -> str:
        return f"organizations/join/{invite_id}"

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
        raw = invite.get("status", OrganizationInviteStatus.pending.value)
        if raw != OrganizationInviteStatus.pending.value:
            return raw
        expires_at = invite.get("expires_at")
        if isinstance(expires_at, datetime):
            exp = (
                expires_at
                if expires_at.tzinfo
                else expires_at.replace(tzinfo=timezone.utc)
            )
            if exp < datetime.now(timezone.utc):
                return OrganizationInviteStatus.expired.value
        return raw

    async def create_invite(self, org_id: str, user_id: str) -> dict[str, Any]:
        org = await self.orgs.assert_org_admin(org_id, user_id)
        if org.get("status") != OrganizationStatus.active.value:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Invites can only be created for active organizations",
            )

        seat_limit = org.get("seat_limit", settings.ORG_SEAT_LIMIT)
        # ponytail: counts active members only, not outstanding pending invites, so an
        # admin can mint more links than remaining seats. accept_invite re-checks.
        # Count pending invites here too if over-issuing becomes a real problem.
        if await self.orgs.active_member_count(org_id) >= seat_limit:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=f"Organization has reached its seat limit of {seat_limit}",
            )

        invite_id = generate_short_id(prefix=self.invite_prefix, type="uuid7")
        now = datetime.now(timezone.utc)
        doc = clean_for_mongodb(
            {
                "_id": invite_id,
                "org_id": org_id,
                "created_by": user_id,
                "method": "link",
                "status": OrganizationInviteStatus.pending.value,
                "expires_at": now + timedelta(hours=settings.ORG_INVITE_TTL_HOURS),
                "accepted_by": None,
                "accepted_at": None,
                "created_at": now,
            }
        )
        await self.db.insert_documents(self.invites_collection, [doc])
        doc["join_path"] = self.invite_join_path(invite_id)
        return doc

    async def list_pending_invites(
        self, org_id: str, user_id: str
    ) -> list[dict[str, Any]]:
        await self.orgs.assert_org_admin(org_id, user_id)
        rows = await self.db.find_documents(
            self.invites_collection,
            {"org_id": org_id, "status": OrganizationInviteStatus.pending.value},
            limit=100,
        )
        out: list[dict[str, Any]] = []
        for row in rows:
            if (
                self._effective_invite_status(row)
                == OrganizationInviteStatus.expired.value
            ):
                continue
            d = dict(row)
            d["join_path"] = self.invite_join_path(row["_id"])
            out.append(d)
        return out

    async def revoke_invite(self, org_id: str, invite_id: str, user_id: str) -> None:
        await self.orgs.assert_org_admin(org_id, user_id)
        invite = await self._load_invite_row(invite_id)
        if invite.get("org_id") != org_id:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND, detail="Invite not found"
            )
        if invite.get("status") != OrganizationInviteStatus.pending.value:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Only pending invites can be revoked",
            )
        # The status guard belongs in the update filter, not just the read above:
        # without it, a revoke racing an in-flight accept overwrites an already-
        # accepted invite, leaving a row that reads "revoked" for someone who joined.
        revoked = await self.db.update_documents(
            self.invites_collection,
            {"_id": invite_id, "status": OrganizationInviteStatus.pending.value},
            {
                "$set": clean_for_mongodb(
                    {"status": OrganizationInviteStatus.revoked.value}
                )
            },
        )
        if not revoked:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Only pending invites can be revoked",
            )

    async def add_member(
        self,
        *,
        org_id: str,
        user_id: str,
        role: str = OrganizationMembershipRole.member.value,
    ) -> dict[str, Any]:
        now = datetime.now(timezone.utc)

        # Look up the row WITHOUT the status filter. `(org_id, user_id)` is uniquely
        # indexed, so a previously-removed member still occupies that key: a blind
        # insert would raise DuplicateKeyError and surface as an opaque 500 the first
        # time a removed member re-joins. Reactivate the existing row instead.
        rows = await self.db.find_documents(
            self.members_collection,
            {"org_id": org_id, "user_id": user_id},
            limit=1,
        )
        if rows:
            existing = rows[0]
            if existing.get("status") == OrganizationMemberStatus.active.value:
                return existing
            await self.db.update_documents(
                self.members_collection,
                {"_id": existing["_id"]},
                {
                    "$set": clean_for_mongodb(
                        {
                            "status": OrganizationMemberStatus.active.value,
                            "role": role,
                            "joined_at": now,
                        }
                    )
                },
            )
            existing.update(
                {
                    "status": OrganizationMemberStatus.active.value,
                    "role": role,
                    "joined_at": now,
                }
            )
            return existing

        # No invited_by / invite_id: decision #5 — provenance is reconstructable from
        # organization_invites.created_by / accepted_by.
        doc = clean_for_mongodb(
            {
                "_id": generate_short_id(prefix=self.member_prefix, type="uuid7"),
                "org_id": org_id,
                "user_id": user_id,
                "role": role,
                "status": OrganizationMemberStatus.active.value,
                "joined_at": now,
            }
        )
        await self.db.insert_documents(self.members_collection, [doc])
        return doc

    async def list_members(self, org_id: str, user_id: str) -> list[dict[str, Any]]:
        """Active members of the org, admin first. Any member may view the roster."""
        await self.orgs.assert_org_member(org_id, user_id)
        rows = await self.db.find_documents(
            self.members_collection,
            {"org_id": org_id, "status": OrganizationMemberStatus.active.value},
            limit=settings.ORG_SEAT_LIMIT * 2,
        )
        rows.sort(
            key=lambda r: (
                r.get("role") != OrganizationMembershipRole.admin.value,
                r.get("joined_at") or datetime.min.replace(tzinfo=timezone.utc),
            )
        )

        out: list[dict[str, Any]] = []
        for row in rows:
            uid = row.get("user_id")
            user = await self.history.get_user(uid) if uid else None
            out.append(
                {
                    "user_id": uid,
                    "role": row.get("role"),
                    "status": row.get("status"),
                    "joined_at": row.get("joined_at"),
                    "first_name": (user or {}).get("first_name"),
                    "last_name": (user or {}).get("last_name"),
                    "username": (user or {}).get("username"),
                    "picture": (user or {}).get("picture"),
                }
            )
        return out

    async def get_invite_preview(self, invite_id: str, user_id: str) -> dict[str, Any]:
        invite = await self._load_invite_row(invite_id)
        org_id = invite["org_id"]
        org = await self.orgs.load_org_row(org_id)
        membership = await self.orgs.get_membership(org_id, user_id)
        member_count = await self.orgs.active_member_count(org_id)
        seat_limit = org.get("seat_limit", settings.ORG_SEAT_LIMIT)

        # Signed here rather than pointing at GET /{org_id}/avatar: that endpoint is
        # membership-gated, and the whole audience for a preview is people who are
        # not members yet. The invite id is what gates this, as it already gates the
        # org name. A signing failure costs the picture, not the preview.
        org_avatar_url = None
        if org.get("avatar_path"):
            try:
                org_avatar_url = get_storage_service().get_signed_url(
                    avatar_blob_key(org_id),
                    expiration_minutes=settings.ORG_AVATAR_SIGNED_URL_MINUTES,
                )
            except Exception:  # noqa: BLE001 — a picture is not worth a failed preview
                logger.warning(
                    f"Avatar signing failed for {org_id}; preview returns no picture",
                    exc_info=True,
                )

        return {
            "invite_id": invite_id,
            "org_id": org_id,
            "org_name": org.get("name", ""),
            "org_avatar_url": org_avatar_url,
            "org_status": org.get("status", OrganizationStatus.active.value),
            "status": self._effective_invite_status(invite),
            "expires_at": invite.get("expires_at"),
            "already_member": membership is not None,
            "seats_remaining": max(seat_limit - member_count, 0),
        }

    async def accept_invite(self, invite_id: str, user_id: str) -> dict[str, Any]:
        # Existence and blocked/archived status are settled by get_current_user_id
        # before any handler runs. Loaded here only for the web_client comparison
        # in the cross-tenant guard below.
        invitee = await self.history.get_user(user_id)

        invite = await self._load_invite_row(invite_id)
        eff_status = self._effective_invite_status(invite)
        org_id = invite["org_id"]

        # Idempotent for anyone already in the org — including the admin who minted
        # the link — so a double-click never 410s a legitimate member.
        existing = await self.orgs.get_membership(org_id, user_id)
        if existing:
            return {
                "org_id": org_id,
                "user_id": user_id,
                "membership_role": existing.get("role"),
                "joined_at": existing.get("joined_at") or datetime.now(timezone.utc),
            }

        if eff_status == OrganizationInviteStatus.expired.value:
            raise HTTPException(
                status_code=status.HTTP_410_GONE, detail="Invite has expired"
            )
        if eff_status == OrganizationInviteStatus.revoked.value:
            raise HTTPException(
                status_code=status.HTTP_410_GONE, detail="Invite has been revoked"
            )
        if eff_status == OrganizationInviteStatus.accepted.value:
            raise HTTPException(
                status_code=status.HTTP_410_GONE,
                detail="This invite link has already been used",
            )
        if eff_status != OrganizationInviteStatus.pending.value:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Invite is not available",
            )

        org = await self.orgs.load_org_row(org_id)
        if org.get("status") != OrganizationStatus.active.value:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Organization is not accepting new members",
            )

        seat_limit = org.get("seat_limit", settings.ORG_SEAT_LIMIT)
        if await self.orgs.active_member_count(org_id) >= seat_limit:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=f"Organization has reached its seat limit of {seat_limit}",
            )

        # Cross-tenant guard, same as ProjectMemberService.accept_invite.
        creator = await self.history.get_user(invite.get("created_by") or "")
        invitee_web = (invitee or {}).get("web_client")
        creator_web = (creator or {}).get("web_client")
        if invitee_web and creator_web and invitee_web != creator_web:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Cannot join an organization from a different product tenant",
            )

        now = datetime.now(timezone.utc)

        # CLAIM THE LINK FIRST, THEN JOIN. This ordering is the single-use gate.
        # `update_documents` wraps `update_one`, so the `status: pending` filter means
        # exactly one of N concurrent accepts gets modified_count == 1; the losers get
        # 0 and are rejected here, before any membership row is written. Doing this
        # after add_member (the way project_member_service does it) lets every racer
        # create a membership and only the winner burn the link.
        #
        # ponytail: if add_member fails after a successful claim, the link is burned
        # with nobody joined and the admin mints a new one. That is the safe direction
        # to fail — the alternative admits two people to a one-time link.
        claimed = await self.db.update_documents(
            self.invites_collection,
            {"_id": invite_id, "status": OrganizationInviteStatus.pending.value},
            {
                "$set": clean_for_mongodb(
                    {
                        "status": OrganizationInviteStatus.accepted.value,
                        "accepted_by": user_id,
                        "accepted_at": now,
                    }
                )
            },
        )
        if not claimed:
            raise HTTPException(
                status_code=status.HTTP_410_GONE,
                detail="This invite link has already been used",
            )

        member_doc = await self.add_member(org_id=org_id, user_id=user_id)

        return {
            "org_id": org_id,
            "user_id": user_id,
            "membership_role": member_doc.get(
                "role", OrganizationMembershipRole.member.value
            ),
            "joined_at": member_doc.get("joined_at") or now,
        }
