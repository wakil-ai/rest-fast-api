"""Cases: enterprise work items stored as `projects` rows marked with `org_id`.

Wraps ProjectService rather than duplicating it, so file upload, OCR, Milvus
indexing, vector search, per-Case instructions and Case-scoped chat sessions all
keep working untouched.
"""

from datetime import datetime, timezone
from typing import Any

from fastapi import HTTPException, status

from core.config import settings
from core.dependencies import (
    get_activity_log_service,
    get_db_manager,
    get_draft_service,
    get_organization_service,
    get_workflow_state_service,
)
from core.logger import logger
from models.activity_logs import EventType
from models.projects import ProjectStatus, is_closed
from models.workflow_states import StateAppliesTo, StateCategory
from utils.user_management import clean_for_mongodb, generate_short_id

_ACTIVE_ONLY: dict[str, Any] = {"archived": {"$ne": True}}

# Set through the ordinary edit path. `status`, `closure`, `org_id` and `owner_id`
# are deliberately absent: lifecycle moves only through the closure endpoints and
# ownership never changes.
_EDITABLE: tuple[str, ...] = (
    "title",
    "description",
    "objective",
    "case_type",
    "state_id",
    "start_date",
    "deadline",
    "assignee_id",
    "suspect",
    "victim",
    "metadata",
)


class CaseService:
    """Org-scoped Cases. Any member creates; owner, assignee or admin edits."""

    def __init__(self) -> None:
        self.db = get_db_manager()
        self.collection = settings.PROJECTS_COLLECTION
        self.prefix = "proj-"

    def _orgs(self):
        return get_organization_service()

    def _states(self):
        return get_workflow_state_service()

    def _logs(self):
        return get_activity_log_service()

    def _drafts(self):
        return get_draft_service()

    def _tasks(self):
        # Imported here, not at module scope: TaskService imports this module.
        from core.dependencies import get_task_service

        return get_task_service()

    async def _log(
        self,
        case: dict[str, Any],
        event: EventType,
        user_id: str,
        payload: dict[str, Any] | None = None,
    ) -> None:
        await self._logs().record(
            org_id=case["org_id"],
            event_type=event,
            actor_id=user_id,
            object_type="case",
            object_id=str(case["_id"]),
            object_label=case.get("title"),
            case_id=str(case["_id"]),
            payload=payload,
        )

    async def ensure_indexes(self) -> None:
        """Called from the lifespan hook — see main.py for why not from __init__.

        Partial on `org_id` existing, so personal projects stay out. `projects`
        holds every personal project of every user in the deployment, and indexing
        those here would carry their weight for no query that reads them. This is
        why `create_project` writes no `org_id` key rather than an explicit null —
        a null is a value, and `$exists` would match it.

        `$exists` rather than the tighter `$type: "string"`: MongoDB only uses a
        partial index when the query is *provably* a subset of the filter, and it
        does not infer "is a string" from an equality match. A `$type` filter builds
        an index the planner then refuses to touch — verified with `explain()`,
        which showed a COLLSCAN on every Case query until this changed. An index
        that is never used is worse than none: all of the write cost, none of the
        benefit.
        """
        await self.db.create_collection(self.collection)
        cases = self.db.mongo_handler.db[self.collection]
        only_cases = {"partialFilterExpression": {"org_id": {"$exists": True}}}
        # Covers _load_case, list_cases and count_using_state, which all lead with
        # org_id — without it every Case read scans the whole projects collection.
        await cases.create_index(
            [("org_id", 1), ("state_id", 1), ("assignee_id", 1)],
            name="case_board",
            **only_cases,
        )
        await cases.create_index(
            [("org_id", 1), ("deadline", 1)], name="case_deadline", **only_cases
        )

    async def _load_case(self, org_id: str, case_id: str) -> dict[str, Any]:
        rows = await self.db.find_documents(
            self.collection,
            {"_id": case_id, "org_id": org_id, **_ACTIVE_ONLY},
            limit=1,
        )
        if not rows:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND, detail="Case not found"
            )
        return rows[0]

    async def is_active(self, org_id: str, case_id: str) -> bool:
        """Whether the Case is still un-archived. No access check — callers that use
        this have already proved access and only want to re-read the flag."""
        rows = await self.db.find_documents(
            self.collection,
            {"_id": case_id, "org_id": org_id, **_ACTIVE_ONLY},
            limit=1,
        )
        return bool(rows)

    async def assert_case_access(
        self, org_id: str, case_id: str, user_id: str
    ) -> dict[str, Any]:
        """Membership is re-checked on every call, so removal revokes access."""
        await self._orgs().assert_org_member(org_id, user_id)
        return await self._load_case(org_id, case_id)

    async def _assert_can_edit(
        self, org_id: str, case: dict[str, Any], user_id: str
    ) -> None:
        """Owner, assignee, or any admin — the confirmed permission rule."""
        if user_id in (case.get("owner_id"), case.get("assignee_id")):
            return
        membership = await self._orgs().get_membership(org_id, user_id)
        if membership and membership.get("role") == "admin":
            return
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Only the Case owner, its assignee, or an admin can edit it",
        )

    async def _assert_assignee_is_member(
        self, org_id: str, assignee_id: str | None
    ) -> None:
        if not assignee_id:
            return
        if not await self._orgs().get_membership(org_id, assignee_id):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Assignee is not an active member of this organization",
            )

    async def _resolve_state(self, org_id: str, state_id: str | None) -> str | None:
        """Validate a chosen state, or fall back to the board's draft column."""
        states = self._states()
        if state_id:
            state = await states.assert_state_usable(
                org_id, state_id, StateAppliesTo.case
            )
            return str(state["_id"])
        # default_state_for seeds an empty board itself.
        default = await states.default_state_for(
            org_id, StateAppliesTo.case, StateCategory.draft
        )
        return str(default["_id"]) if default else None

    async def create_case(
        self, org_id: str, user_id: str, body: dict[str, Any]
    ) -> dict[str, Any]:
        await self._orgs().assert_org_member(org_id, user_id)

        title = (body.get("title") or "").strip()
        if not title:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST, detail="Title is required"
            )
        await self._assert_assignee_is_member(org_id, body.get("assignee_id"))
        state_id = await self._resolve_state(org_id, body.get("state_id"))

        now = datetime.now(timezone.utc)
        case_id = generate_short_id(prefix=self.prefix, type="uuid7")
        doc = clean_for_mongodb(
            {
                "_id": case_id,
                "org_id": org_id,
                "owner_id": user_id,
                "title": title,
                "description": body.get("description"),
                "objective": body.get("objective"),
                "case_type": body.get("case_type"),
                "state_id": state_id,
                "start_date": body.get("start_date"),
                "deadline": body.get("deadline"),
                "assignee_id": body.get("assignee_id"),
                "suspect": body.get("suspect"),
                "victim": body.get("victim"),
                "closure": {},
                "updated_by": user_id,
                # Existing project machinery reads these; keep the shape identical.
                "files": [],
                "status": ProjectStatus.active.value,
                "stats": {"docs": 0, "chats": 0, "reminders": 0},
                "settings": {},
                "metadata": body.get("metadata") or {},
                "archived": False,
                "created_at": now,
                "updated_at": now,
            }
        )
        await self.db.insert_documents(self.collection, [doc])
        # Logged after the write succeeds, never before.
        await self._log(doc, EventType.case_created, user_id)
        logger.info(f"Created case {case_id} in organization {org_id}")
        return doc

    async def list_cases(
        self,
        org_id: str,
        user_id: str,
        *,
        state_id: str | None = None,
        assignee_id: str | None = None,
        case_status: ProjectStatus | None = None,
        limit: int = 50,
        skip: int = 0,
    ) -> list[dict[str, Any]]:
        await self._orgs().assert_org_member(org_id, user_id)

        query: dict[str, Any] = {"org_id": org_id, **_ACTIVE_ONLY}
        if state_id:
            query["state_id"] = state_id
        if assignee_id:
            query["assignee_id"] = assignee_id
        if case_status is not None:
            query["status"] = case_status.value
        return await self.db.find_documents(
            self.collection, query, limit=limit, skip=skip
        )

    async def get_case(self, org_id: str, case_id: str, user_id: str) -> dict[str, Any]:
        return await self.assert_case_access(org_id, case_id, user_id)

    async def update_case(
        self, org_id: str, case_id: str, user_id: str, updates: dict[str, Any]
    ) -> dict[str, Any]:
        case = await self.assert_case_access(org_id, case_id, user_id)
        await self._assert_can_edit(org_id, case, user_id)

        set_fields: dict[str, Any] = {}
        for key in _EDITABLE:
            if key not in updates:
                continue
            value = updates[key]
            if key == "title":
                title = (value or "").strip()
                if not title:
                    raise HTTPException(
                        status_code=status.HTTP_400_BAD_REQUEST,
                        detail="Title is required",
                    )
                set_fields["title"] = title
            elif key == "state_id":
                # Not `and value`: falling through on an explicit null would write
                # state_id: None unvalidated, dropping the Case off every board and
                # out of count_using_state — so archive_state would then happily
                # delete a column that still holds work.
                if not value:
                    raise HTTPException(
                        status_code=status.HTTP_400_BAD_REQUEST,
                        detail="A Case always sits in a workflow state",
                    )
                # Only a *change* is validated. A client that PATCHes the whole form
                # back sends the state it just displayed, and on a closed Case that
                # value is the closed column — which assert_state_usable refuses.
                # Validating a no-op would make every field of a closed Case
                # uneditable, and would log a state_changed event for a move that
                # never happened.
                if value != case.get("state_id"):
                    # A closed Case's board position is frozen. assert_state_usable
                    # refuses the closed column, so allowing a move *off* it would be
                    # one-way: the Case would show as in progress while `status` said
                    # closed, and nothing could put it back. Reopening is the way out.
                    if is_closed(case):
                        raise HTTPException(
                            status_code=status.HTTP_409_CONFLICT,
                            detail=(
                                "A closed Case stays on the closed column. Reopen it "
                                "first if its work is not finished after all."
                            ),
                        )
                    state = await self._states().assert_state_usable(
                        org_id, value, StateAppliesTo.case
                    )
                    set_fields["state_id"] = str(state["_id"])
            elif key == "assignee_id":
                await self._assert_assignee_is_member(org_id, value)
                set_fields["assignee_id"] = value
            elif key == "metadata":
                set_fields["metadata"] = value or {}
            else:
                set_fields[key] = value

        row = await self._apply(case, set_fields, user_id)
        await self._logs().record_changes(
            kind="case",
            previous=case,
            set_fields=set_fields,
            actor_id=user_id,
            case_id=str(case["_id"]),
        )
        return row

    async def archive_case(
        self, org_id: str, case_id: str, user_id: str
    ) -> dict[str, Any]:
        """Admin only — removing an organization's work is the Head's call.

        Idempotent: an already-archived Case is loaded and re-archived rather than
        404ing. The Case is written before its Tasks so that `create_task`'s access
        check starts failing immediately, which shrinks the window for a new Task
        landing mid-cascade to the gap inside a single create — `create_task` closes
        that gap itself by re-reading the flag after its insert. The cost of this
        ordering is that a failed cascade leaves the Case archived with live Tasks,
        so retrying the DELETE has to be able to finish the job, and `_load_case`'s
        active-only filter would otherwise refuse the retry outright.
        """
        await self._orgs().assert_org_admin(org_id, user_id)
        rows = await self.db.find_documents(
            self.collection, {"_id": case_id, "org_id": org_id}, limit=1
        )
        if not rows:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND, detail="Case not found"
            )
        case = rows[0]
        set_fields: dict[str, Any] = {"archived": True}
        # `archived` is the flag every read filters on; `status` is the lifecycle
        # marker. Overwriting a closed Case's status would erase the fact that its
        # work was signed off, and there is no un-archive route to recover it —
        # `_load_case` filters archived rows, so `reopen` 404s. Archiving a closed
        # Case is filing it away, not undoing the closure.
        if not is_closed(case):
            set_fields["status"] = ProjectStatus.archived.value
        row = await self._apply(case, set_fields, user_id)
        # Logged before the cascade, not after. The guard below keys off the
        # pre-write snapshot, so a cascade that throws would leave the Case archived
        # with no event — and the retry, seeing `archived: True`, would skip the log
        # for good. An evidence record must not be lost to a transient Mongo error.
        if not case.get("archived"):
            await self._log(case, EventType.case_archived, user_id)
        # The Tasks go with it. Left behind they would keep showing up in the org-wide
        # "my work" list pointing at a Case that now 404s, and would keep blocking
        # archive_state with a count nobody can act on.
        await self._tasks().archive_for_case(org_id, str(case["_id"]), user_id)
        return row

    async def request_closure(
        self, org_id: str, case_id: str, user_id: str
    ) -> dict[str, Any]:
        case = await self.assert_case_access(org_id, case_id, user_id)
        await self._assert_can_edit(org_id, case, user_id)
        # Nothing closes over unconfirmed AI work. No task_id, so a draft on any
        # child Task blocks the parent Case too — which is why Task draft rows
        # carry case_id.
        await self._drafts().assert_no_active_draft(org_id, case_id)

        if is_closed(case):
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT, detail="Case is already closed"
            )
        closure = {
            **(case.get("closure") or {}),
            "requested_by": user_id,
            "requested_at": datetime.now(timezone.utc),
        }
        row = await self._apply(case, {"closure": closure}, user_id)
        await self._log(case, EventType.closure_requested, user_id)
        return row

    async def approve_closure(
        self, org_id: str, case_id: str, user_id: str
    ) -> dict[str, Any]:
        """The single writer of the closed lifecycle: status and state move together.

        An admin may approve a closure they requested themselves — a six-person
        department with one Head has nobody else to ask.
        """
        await self._orgs().assert_org_admin(org_id, user_id)
        case = await self._load_case(org_id, case_id)
        # Nothing closes over unconfirmed AI work — a draft on any child Task
        # blocks the parent Case too.
        await self._drafts().assert_no_active_draft(org_id, case_id)

        closure = dict(case.get("closure") or {})
        if not closure.get("requested_at"):
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="Closure has not been requested for this Case",
            )
        if closure.get("approved_at"):
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT, detail="Case is already closed"
            )

        now = datetime.now(timezone.utc)
        closure["approved_by"] = user_id
        closure["approved_at"] = now

        set_fields: dict[str, Any] = {
            "closure": closure,
            "status": ProjectStatus.closed.value,
        }
        # The board must agree with the lifecycle, so the state moves in the same
        # write. Matched by category, never by name — the column may be renamed.
        closed_state = await self._states().default_state_for(
            org_id, StateAppliesTo.case, StateCategory.closed
        )
        if closed_state:
            set_fields["state_id"] = str(closed_state["_id"])
        row = await self._apply(case, set_fields, user_id)
        await self._log(
            case,
            EventType.closure_approved,
            user_id,
            {"requested_by": closure.get("requested_by")},
        )
        return row

    async def reopen(self, org_id: str, case_id: str, user_id: str) -> dict[str, Any]:
        """Undo a closure. Admin only — the Head signed it off, the Head takes it back.

        Without this a closed Case is a dead end: `update_case` refuses to move it
        off the closed column, and `approve_closure` refuses to run twice. Clearing
        the approval is what makes the closed lifecycle reversible rather than a
        one-way trap.

        The closure block is cleared, not amended: `requested_at` goes too, so the
        next closure starts from a fresh request rather than inheriting a stale one
        and letting an admin approve something nobody asked for. The event log keeps
        the history — the approval that happened is still on the timeline.
        """
        await self._orgs().assert_org_admin(org_id, user_id)
        case = await self._load_case(org_id, case_id)

        closure = case.get("closure") or {}
        if not closure.get("approved_at"):
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT, detail="Case is not closed"
            )

        set_fields: dict[str, Any] = {
            "closure": {},
            "status": ProjectStatus.active.value,
        }
        # Back to the board's draft column, matched by category. Leaving it on the
        # closed column would reopen a Case that still reads as done everywhere.
        draft = await self._states().default_state_for(
            org_id, StateAppliesTo.case, StateCategory.draft
        )
        if draft:
            set_fields["state_id"] = str(draft["_id"])
        row = await self._apply(case, set_fields, user_id)
        await self._log(
            case,
            EventType.closure_reopened,
            user_id,
            {"approved_by": closure.get("approved_by")},
        )
        return row

    async def count_using_state(self, org_id: str, state_id: str) -> int:
        """How many live Cases sit in a column — the archive guard's input.

        Counted in the server, not by fetching: the number is shown to the admin, so
        a `limit` would silently cap it, and pulling whole Case documents to compute
        an integer is pure waste.
        """
        return await self.db.mongo_handler.db[self.collection].count_documents(
            {"org_id": org_id, "state_id": state_id, **_ACTIVE_ONLY}
        )

    async def set_state(
        self, org_id: str, case_id: str, state_id: str, user_id: str
    ) -> dict[str, Any]:
        """Move a Case to a state, for callers outside this service.

        Exists so DraftService's board move does not have to reach into
        ``_load_case`` and ``_apply`` — private access that duplicated this
        service's update rules in a third place and would break silently the
        moment they changed. Deliberately does no *permission* check: the caller
        has already authorized the action that triggers the move.

        The closure freeze is not a permission, so it is enforced here. A closed
        Case's column is one-way — `assert_state_usable` refuses the closed
        column, so anything moved off it can never go back, and only an admin
        `reopen` can repair the record. That has to hold for every caller, not
        just the ones that happen to route through `update_case`.
        """
        case = await self._load_case(org_id, case_id)
        if is_closed(case):
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=(
                    "A closed Case stays on the closed column. Reopen it "
                    "first if its work is not finished after all."
                ),
            )
        return await self._apply(case, {"state_id": state_id}, user_id)

    async def _apply(
        self, case: dict[str, Any], set_fields: dict[str, Any], user_id: str
    ) -> dict[str, Any]:
        row = dict(case)
        if not set_fields:
            return row
        set_fields = {
            **set_fields,
            "updated_by": user_id,
            "updated_at": datetime.now(timezone.utc),
        }
        await self.db.update_documents(
            self.collection,
            {"_id": case["_id"], "org_id": case["org_id"]},
            {"$set": clean_for_mongodb(set_fields)},
        )
        row.update(set_fields)
        return row
