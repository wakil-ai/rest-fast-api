"""Append-only audit trail for admin subscription changes.

Manual subscription edits are money-equivalent, so every attempt — including the
ones that fail — leaves a record. Nothing in the API mutates or deletes rows here.

The ``request_id`` unique index doubles as the idempotency guard: a claim is
written *before* the mutation runs, so a replayed request collides on insert and
is rejected instead of applying twice.
"""

import hashlib
import time
import uuid
from typing import Any

from pymongo.errors import DuplicateKeyError

from core.config import settings
from core.dependencies import get_mongo_handler
from core.logger import logger

# Actions this service records. Kept as plain strings (not an Enum) to match the
# `action` filter on the audit query endpoint.
ACTION_GRANT = "subscription.grant"
ACTION_EXTEND = "subscription.extend"
ACTION_ADJUST_CREDITS = "subscription.adjust_credits"
ACTION_REVOKE = "subscription.revoke"

RESULT_IN_PROGRESS = "in_progress"
RESULT_SUCCESS = "success"
RESULT_ERROR = "error"


class AdminActionAlreadyRecorded(Exception):
    """A claim for this ``request_id`` already exists.

    Carries the original entry so the route can tell the operator what happened
    the first time rather than just refusing.
    """

    def __init__(self, existing: dict[str, Any]):
        self.existing = existing
        super().__init__(f"Admin action {existing.get('request_id')} already recorded")


class AdminAuditService:
    def __init__(self):
        self.mongo_handler = get_mongo_handler()
        self.collection_name = settings.ADMIN_AUDIT_LOGS_COLLECTION
        self._indexes_ready = False

    @property
    def _collection(self):
        return self.mongo_handler.db[self.collection_name]

    def _now_ms(self) -> int:
        return int(time.time() * 1000)

    async def ensure_indexes(self) -> None:
        if self._indexes_ready:
            return

        try:
            await self._collection.create_index(
                [("request_id", 1)],
                unique=True,
                partialFilterExpression={"request_id": {"$type": "string"}},
            )
            await self._collection.create_index([("created_at_ms", -1)])
            await self._collection.create_index(
                [("target_user_id", 1), ("created_at_ms", -1)]
            )
            await self._collection.create_index(
                [("actor.operator", 1), ("created_at_ms", -1)]
            )
            self._indexes_ready = True
        except Exception as exc:
            logger.warning(f"[AdminAuditService] Failed to ensure indexes: {exc}")

    @staticmethod
    def key_fingerprint() -> str:
        """Short digest of the super-admin key that authorized the action.

        The operator name is self-reported; this is not. Once per-operator keys
        exist, the fingerprint is what maps an entry back to a real person.
        """
        return hashlib.sha256(settings.SUPER_ADMIN_API_KEY.encode()).hexdigest()[:12]

    async def claim(
        self,
        *,
        request_id: str,
        action: str,
        target_user_id: str,
        operator: str,
        reason: str,
        params: dict[str, Any],
        source_ip: str | None = None,
        user_agent: str | None = None,
    ) -> str:
        """Reserve ``request_id`` before mutating anything.

        Returns the audit ``event_id`` to pass to :meth:`finalize`. Raises
        :class:`AdminActionAlreadyRecorded` when the id was already used.
        """
        await self.ensure_indexes()

        now_ms = self._now_ms()
        event_id = uuid.uuid4().hex
        document = {
            "event_id": event_id,
            "request_id": request_id,
            "created_at_ms": now_ms,
            "action": action,
            "target_user_id": target_user_id,
            "actor": {
                "operator": operator,
                # Recorded so a reader never mistakes a header value for proof.
                "operator_source": "header",
                "key_fingerprint": self.key_fingerprint(),
                "source_ip": source_ip,
                "user_agent": user_agent,
            },
            "reason": reason,
            "params": params,
            "before": None,
            "after": None,
            "result": RESULT_IN_PROGRESS,
            "error_code": None,
            "duration_ms": None,
        }

        try:
            await self._collection.insert_one(document)
        except DuplicateKeyError:
            existing = await self._collection.find_one({"request_id": request_id})
            raise AdminActionAlreadyRecorded(existing or {"request_id": request_id})

        return event_id

    async def finalize(
        self,
        event_id: str,
        *,
        result: str,
        before: dict[str, Any] | None = None,
        after: dict[str, Any] | None = None,
        error_code: str | None = None,
    ) -> dict[str, Any] | None:
        """Close out a claimed entry.

        A failure here must not mask the outcome of the action itself, so it is
        logged rather than raised — the same rule ``ActivityLogService`` follows.
        """
        try:
            entry = await self._collection.find_one({"event_id": event_id})
            duration_ms = (
                self._now_ms() - int(entry["created_at_ms"])
                if entry and entry.get("created_at_ms") is not None
                else None
            )
            return await self._collection.find_one_and_update(
                {"event_id": event_id},
                {
                    "$set": {
                        "result": result,
                        "before": before,
                        "after": after,
                        "error_code": error_code,
                        "duration_ms": duration_ms,
                    }
                },
                return_document=True,
            )
        except Exception as exc:
            logger.error(
                f"[AdminAuditService] Failed to finalize audit entry {event_id}: {exc}"
            )
            return None

    async def list_entries(
        self,
        *,
        user_id: str | None = None,
        operator: str | None = None,
        action: str | None = None,
        from_ms: int | None = None,
        to_ms: int | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> dict[str, Any]:
        await self.ensure_indexes()

        query: dict[str, Any] = {}
        if user_id:
            query["target_user_id"] = user_id
        if operator:
            query["actor.operator"] = operator
        if action:
            query["action"] = action
        if from_ms is not None or to_ms is not None:
            window: dict[str, int] = {}
            if from_ms is not None:
                window["$gte"] = int(from_ms)
            if to_ms is not None:
                window["$lte"] = int(to_ms)
            query["created_at_ms"] = window

        total = await self._collection.count_documents(query)
        cursor = (
            self._collection.find(query)
            .sort("created_at_ms", -1)
            .skip(max(0, offset))
            .limit(limit)
        )
        items = await cursor.to_list(length=limit)
        for item in items:
            item.pop("_id", None)

        return {"items": items, "total": total, "limit": limit, "offset": offset}
