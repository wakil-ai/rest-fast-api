"""Account archive (soft-delete)

Idempotently marks a user and all user-owned data as ``archived`` in-place, for the
Google Play "delete my account" flow. Does NOT hard-delete anything, and does NOT
touch financial/tax collections (those follow a separate 1-year retention job).
"""

from datetime import datetime, timezone
from typing import Any

from core.config import settings
from core.dependencies import get_db_manager
from core.exceptions import InvalidInputError, UserNotFoundError
from core.logger import logger
from services.meta_capi_service import AD_ATTRIBUTION_FIELDS
from services.project_service import PERSONAL_SCOPE

_BSON_INT64_MIN = -(2**63)
_BSON_INT64_MAX = 2**63 - 1


class AccountArchiveService:
    """Archive a user account and all owned data, idempotently and safe to retry."""

    def __init__(self):
        self.db_manager = get_db_manager()
        self.users_collection = settings.USERS_COLLECTION

        # User-owned collections -> the field that keys a document to its owner.
        # `users` is handled separately (flipped LAST). Payment/tax collections are
        # intentionally excluded (kept under retention, purged by a separate job).
        # `referral_sources` is excluded: it is a global per-source aggregate, not
        # user-owned data.
        self._owned_collections: dict[str, str] = {
            settings.SESSIONS_COLLECTION: "user_id",
            settings.MESSAGES_COLLECTION: "user_id",
            settings.FILES_COLLECTION: "user_id",
            settings.PROJECTS_COLLECTION: "owner_id",
            settings.PROJECT_MEMBERS_COLLECTION: "user_id",
            settings.PROJECT_INVITES_COLLECTION: "created_by",
            settings.RATE_LIMIT_COLLECTION: "user_id",  # creditusage
            settings.SUBSCRIPTIONS_COLLECTION: "user_id",
            settings.DAILY_SUBSCRIPTIONS_COLLECTION: "user_id",
            settings.USER_PROMO_CODE_COLLECTION: "user_id",
            settings.TOKEN_COUNTING_COLLECTION: "user_id",
            settings.TELEGRAM_CHATS_COLLECTION: "user_id",  # stored as int (see below)
            settings.FINGERPRINTS_COLLECTION: "user_id",
        }
        # Enterprise collections are deliberately absent, and this is not an
        # oversight to be "fixed" by adding them:
        #
        #   tasks, workflow_states, organizations, organization_members —
        #     owned by the organization, not by any one member. A person deleting
        #     their personal WakilAI account must not take the department's board
        #     and assignments with them. The same reasoning that scopes `projects`
        #     above, taken to its conclusion: these have no personal half at all.
        #   activity_logs — ZRU-1115 evidence. Append-only, never archived.
        #   drafts — the same. A member deleting their personal account must not
        #     archive the drafts their organization approved; `created_by`
        #     pointing at a departed member is the intended state, not a leak.
        #
        # A departed member leaves rows referencing their id. That is intended:
        # the log has to stay readable, and the org still needs the work.

    @staticmethod
    def _owner_values(user_id: str) -> list[Any]:
        """Candidate owner values to match on.

        Internal user_ids are strings, but some collections (e.g. telegram_chats)
        store the Telegram id as an int. When the id is all-digits we match both the
        string and int forms so those rows are caught too. The int form can only
        match int-typed fields, so this never produces false matches on string keys.
        """
        values: list[Any] = [user_id]
        if isinstance(user_id, str) and user_id.isdigit():
            numeric_user_id = int(user_id)
            # PyMongo encodes Python ints as signed BSON int64 values. Some identity
            # providers issue digit-only IDs that exceed that range; those IDs are
            # stored and matched as strings and must not be included as Python ints.
            if _BSON_INT64_MIN <= numeric_user_id <= _BSON_INT64_MAX:
                values.append(numeric_user_id)
        return values

    async def archive_user_account(
        self,
        user_id: str,
        *,
        reason: str = "user_request",
        source: str = "delete_account_endpoint",
    ) -> dict[str, Any]:
        """Idempotently archive ``user_id`` and all owned data.

        Returns a summary distinguishing a fresh archive from an already-archived
        account. Raises ``UserNotFoundError`` if the user does not exist.
        """
        if not user_id or not str(user_id).strip():
            raise InvalidInputError("user_id is required")

        db = self.db_manager.mongo_handler.db
        now = datetime.now(timezone.utc)

        # Snapshot pre-state: is the account already archived? (idempotency reporting)
        user_doc = await db[self.users_collection].find_one({"_id": user_id})
        if not user_doc:
            raise UserNotFoundError(user_id)
        already_archived = bool(user_doc.get("archived"))

        stamp = {
            "archived": True,
            "archived_at": now,
            "deletion_requested_at": now,
            "archive_reason": reason,
            "archive_source": source,
        }
        owner_values = self._owner_values(user_id)

        # 1) Sweep every owned collection. The `archived: {$ne: True}` predicate makes
        #    each write a no-op on re-run, so the whole operation is idempotent and
        #    safe to retry after a partial/crashed run.
        results: dict[str, int] = {}
        for collection_name, owner_field in self._owned_collections.items():
            query: dict[str, Any] = {
                owner_field: {"$in": owner_values},
                "archived": {"$ne": True},
            }
            # These four collections all hold enterprise rows alongside personal
            # ones, keyed by the same owner. Unscoped, a member deleting their own
            # WakilAI account would take the whole organization's data with them:
            # the Cases they opened off the board, the Case conversations they
            # started, those conversations' transcripts, and the evidence files they
            # uploaded to a Case. Someone else's data, erased by a personal action.
            #
            # Each carries `org_id`, so one predicate separates all four. Messages
            # take it from their session and Case files from their project, both at
            # write time — rows written before that stamp existed have no `org_id`
            # and read as personal, which is what they were.
            #
            # The reads that make the loss visible: `get_sessions_by_project` and
            # `get_messages` filter archived rows, and `get_file_by_id` backs the
            # fetch, download and delete routes plus the file context injected into
            # chat. A Case would keep its session list while every transcript came
            # back empty, and keep listing files that 404 on open.
            #
            # Compared here rather than through a lookup map: both sides then read
            # the same setting at the same moment, so the scope cannot silently go
            # missing if that setting is patched or reloaded.
            if collection_name in (
                settings.PROJECTS_COLLECTION,
                settings.SESSIONS_COLLECTION,
                settings.MESSAGES_COLLECTION,
                settings.FILES_COLLECTION,
            ):
                query.update(PERSONAL_SCOPE)
            res = await db[collection_name].update_many(query, {"$set": stamp})
            results[collection_name] = res.modified_count

        # 2) Flip the `users` doc LAST, so a crash mid-sweep leaves a retryable state
        #    (user not yet blocked) rather than a half-archived, already-blocked user.
        # The user doc is kept, so the advertising IDs must go explicitly.
        user_res = await db[self.users_collection].update_one(
            {"_id": user_id, "archived": {"$ne": True}},
            {
                "$set": stamp,
                "$unset": {field: "" for field in AD_ATTRIBUTION_FIELDS},
            },
        )
        results[self.users_collection] = user_res.modified_count

        logger.info(
            f"[AccountArchive] user_id={user_id} already_archived={already_archived} "
            f"modified={results}"
        )

        return {
            "user_id": user_id,
            "already_archived": already_archived,
            "archived_at": user_doc.get("archived_at") if already_archived else now,
            "collections": results,
        }
