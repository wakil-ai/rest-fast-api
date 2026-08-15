"""Append-only activity log.

Deliberately exposes no update and no delete. This is ZRU-1115 evidence that a
human was involved, so a correction is a new event, never an edit to an old one.
A test asserts the absence of those methods, so adding one fails the suite.
"""

from datetime import datetime, timezone
from typing import Any

from fastapi import HTTPException, status

from core.config import settings
from core.dependencies import (
    get_chat_history_service,
    get_db_manager,
    get_organization_service,
)
from core.logger import logger
from models.activity_logs import ActorType, EventType
from utils.user_management import clean_for_mongodb, generate_short_id


class ActivityLogService:
    """Writes are best-effort; reads are org-scoped and paginated on `_id`."""

    def __init__(self) -> None:
        self.db = get_db_manager()
        self.collection = settings.ACTIVITY_LOGS_COLLECTION
        self.prefix = "alog-"

    async def ensure_indexes(self) -> None:
        """Called from the lifespan hook — see main.py for why not from __init__."""
        await self.db.create_collection(self.collection)
        logs = self.db.mongo_handler.db[self.collection]
        await logs.create_index([("org_id", 1), ("_id", -1)], name="org_timeline")
        await logs.create_index(
            [("org_id", 1), ("case_id", 1), ("_id", -1)], name="org_case_timeline"
        )
        await logs.create_index([("actor.id", 1), ("_id", -1)], name="actor_timeline")

    async def record(
        self,
        *,
        org_id: str,
        event_type: EventType,
        actor_id: str,
        object_type: str,
        object_id: str,
        object_label: str | None = None,
        case_id: str | None = None,
        task_id: str | None = None,
        payload: dict[str, Any] | None = None,
        actor_type: ActorType = ActorType.user,
        on_behalf_of: str | None = None,
    ) -> dict[str, Any] | None:
        """Append one event. Never raises.

        A failure to log must not fail the action being logged — an audit trail
        that can take down Case creation is worse than a gap in the trail. The
        write is awaited inside the guard rather than fired and forgotten, because
        an unawaited coroutine that raises after the response is invisible.
        """
        try:
            now = datetime.now(timezone.utc)
            doc = clean_for_mongodb(
                {
                    "_id": generate_short_id(prefix=self.prefix, type="uuid7"),
                    "org_id": org_id,
                    "occurred_at": now,
                    "event_type": event_type.value,
                    "actor": await self._actor_snapshot(org_id, actor_id, actor_type),
                    "on_behalf_of": on_behalf_of,
                    "object": {
                        "type": object_type,
                        "id": object_id,
                        "label": object_label,
                    },
                    "case_id": case_id,
                    "task_id": task_id,
                    "payload": payload or {},
                }
            )
            await self.db.insert_documents(self.collection, [doc])
            return doc
        except Exception as exc:
            logger.error(
                f"Failed to record {event_type.value} for org {org_id}: {exc}",
                exc_info=True,
            )
            return None

    async def record_changes(
        self,
        *,
        kind: str,
        previous: dict[str, Any],
        set_fields: dict[str, Any],
        actor_id: str,
        case_id: str,
        task_id: str | None = None,
    ) -> None:
        """Turn one edit into the events a timeline actually needs.

        A state move and a reassignment are the two things dashboards and the Head
        care about, so they get their own event types rather than being buried in a
        generic "updated". One PATCH can legitimately produce several events.
        `metadata` is excluded from the changed-field list: it is a free-form bag
        and listing its keys says nothing useful.
        """
        events = {
            "case": (
                EventType.case_state_changed,
                EventType.case_assigned,
                EventType.case_updated,
            ),
            "task": (
                EventType.task_state_changed,
                EventType.task_assigned,
                EventType.task_updated,
            ),
        }[kind]
        state_event, assign_event, update_event = events
        entity_id = str(previous["_id"])

        async def emit(event: EventType, payload: dict[str, Any]) -> None:
            await self.record(
                org_id=previous["org_id"],
                event_type=event,
                actor_id=actor_id,
                object_type=kind,
                object_id=entity_id,
                object_label=set_fields.get("title") or previous.get("title"),
                case_id=case_id,
                task_id=task_id,
                payload=payload,
            )

        # `from` matters as much as `to` — a state change without the previous
        # value is half a fact.
        if "state_id" in set_fields and set_fields["state_id"] != previous.get(
            "state_id"
        ):
            await emit(
                state_event,
                {"from": previous.get("state_id"), "to": set_fields["state_id"]},
            )
        if "assignee_id" in set_fields and set_fields["assignee_id"] != previous.get(
            "assignee_id"
        ):
            await emit(
                assign_event,
                {"from": previous.get("assignee_id"), "to": set_fields["assignee_id"]},
            )

        other = sorted(
            set(set_fields)
            - {"state_id", "assignee_id", "updated_by", "updated_at", "metadata"}
        )
        if other:
            await emit(update_event, {"fields": other})

    async def _actor_snapshot(
        self, org_id: str, actor_id: str, actor_type: ActorType
    ) -> dict[str, Any]:
        """Role and display name as they were at this moment, not as a join.

        Resolved on write so the log still reads correctly after the member's role
        changes or their account is removed.
        """
        role: str | None = None
        name: str | None = None
        if actor_type is ActorType.user:
            membership = await get_organization_service().get_membership(
                org_id, actor_id
            )
            role = (membership or {}).get("role")
            user = await get_chat_history_service().get_user(actor_id)
            if user:
                name = " ".join(
                    part
                    for part in (user.get("first_name"), user.get("last_name"))
                    if part
                ) or user.get("username")
        return {"id": actor_id, "type": actor_type.value, "role": role, "name": name}

    async def list_logs(
        self,
        org_id: str,
        user_id: str,
        *,
        case_id: str | None = None,
        task_id: str | None = None,
        limit: int = 50,
        before: str | None = None,
    ) -> list[dict[str, Any]]:
        """Newest first. Any active member may read their organization's timeline."""
        await get_organization_service().assert_org_member(org_id, user_id)

        query: dict[str, Any] = {"org_id": org_id}
        if case_id:
            query["case_id"] = case_id
        if task_id:
            query["task_id"] = task_id
        if before:
            # `_id` is a uuid7, so it sorts chronologically and doubles as the
            # pagination cursor — no separate sort key needed.
            query["_id"] = {"$lt": before}

        # Queried directly rather than through db.find_documents, which forces
        # `.sort("created_at", -1)` on every read. These documents have no
        # `created_at`, so that sort would order them arbitrarily.
        cursor = (
            self.db.mongo_handler.db[self.collection]
            .find(query)
            .sort("_id", -1)
            .limit(limit)
        )
        return await cursor.to_list(length=limit)

    async def get_log(self, org_id: str, log_id: str, user_id: str) -> dict[str, Any]:
        await get_organization_service().assert_org_member(org_id, user_id)
        rows = await self.db.find_documents(
            self.collection, {"_id": log_id, "org_id": org_id}, limit=1
        )
        if not rows:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND, detail="Activity log not found"
            )
        return rows[0]
