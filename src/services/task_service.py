"""Tasks: assignments hanging off a Case, in their own collection."""

from datetime import datetime, timezone
from typing import Any

from fastapi import HTTPException, status

from core.config import settings
from core.dependencies import (
    get_activity_log_service,
    get_case_service,
    get_db_manager,
    get_draft_service,
    get_organization_service,
    get_workflow_state_service,
)
from core.logger import logger
from models.activity_logs import EventType
from models.projects import is_closed
from models.workflow_states import StateAppliesTo, StateCategory
from utils.user_management import clean_for_mongodb, generate_short_id

_ACTIVE_ONLY: dict[str, Any] = {"archived": {"$ne": True}}

# `closure`, `org_id`, `case_id` and `created_by` are deliberately absent: closure
# moves only through its own endpoints, and a Task never changes parent or owner.
_EDITABLE: tuple[str, ...] = (
    "title",
    "description",
    "objective",
    "task_type",
    "state_id",
    "start_date",
    "deadline",
    "assignee_id",
    "ai_brief",
)


class TaskService:
    """Org-scoped Tasks. Same permission rules as Cases."""

    def __init__(self) -> None:
        self.db = get_db_manager()
        self.collection = settings.TASKS_COLLECTION
        self.prefix = "task-"

    def _orgs(self):
        return get_organization_service()

    def _states(self):
        return get_workflow_state_service()

    def _cases(self):
        return get_case_service()

    def _logs(self):
        return get_activity_log_service()

    def _drafts(self):
        return get_draft_service()

    async def _log(
        self,
        task: dict[str, Any],
        event: EventType,
        user_id: str,
        payload: dict[str, Any] | None = None,
    ) -> None:
        await self._logs().record(
            org_id=task["org_id"],
            event_type=event,
            actor_id=user_id,
            object_type="task",
            object_id=str(task["_id"]),
            object_label=task.get("title"),
            case_id=task.get("case_id"),
            task_id=str(task["_id"]),
            payload=payload,
        )

    async def ensure_indexes(self) -> None:
        """Called from the lifespan hook — see main.py for why not from __init__."""
        await self.db.create_collection(self.collection)
        tasks = self.db.mongo_handler.db[self.collection]
        await tasks.create_index([("case_id", 1)], name="task_case")
        await tasks.create_index(
            [("org_id", 1), ("assignee_id", 1), ("state_id", 1)], name="task_board"
        )
        await tasks.create_index([("org_id", 1), ("deadline", 1)], name="task_deadline")

    async def _load_task(self, org_id: str, task_id: str) -> dict[str, Any]:
        rows = await self.db.find_documents(
            self.collection,
            {"_id": task_id, "org_id": org_id, **_ACTIVE_ONLY},
            limit=1,
        )
        if not rows:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND, detail="Task not found"
            )
        return rows[0]

    async def _assert_can_edit(
        self, org_id: str, task: dict[str, Any], user_id: str
    ) -> None:
        if user_id in (task.get("created_by"), task.get("assignee_id")):
            return
        membership = await self._orgs().get_membership(org_id, user_id)
        if membership and membership.get("role") == "admin":
            return
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Only the Task creator, its assignee, or an admin can edit it",
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

    async def create_task(
        self, org_id: str, case_id: str, user_id: str, body: dict[str, Any]
    ) -> dict[str, Any]:
        # Loading the parent through CaseService proves it exists, belongs to this
        # organization and is not archived — so org_id comes from the Case rather
        # than the request body and the two can never disagree.
        case = await self._cases().assert_case_access(org_id, case_id, user_id)

        title = (body.get("title") or "").strip()
        if not title:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST, detail="Title is required"
            )
        await self._assert_assignee_is_member(org_id, body.get("assignee_id"))

        state_id = body.get("state_id")
        if state_id:
            state = await self._states().assert_state_usable(
                org_id, state_id, StateAppliesTo.task
            )
            state_id = str(state["_id"])
        else:
            default = await self._states().default_state_for(
                org_id, StateAppliesTo.task, StateCategory.draft
            )
            state_id = str(default["_id"]) if default else None

        now = datetime.now(timezone.utc)
        task_id = generate_short_id(prefix=self.prefix, type="uuid7")
        doc = clean_for_mongodb(
            {
                "_id": task_id,
                "case_id": str(case["_id"]),
                "org_id": org_id,
                "title": title,
                "description": body.get("description"),
                "objective": body.get("objective"),
                "task_type": body.get("task_type"),
                "state_id": state_id,
                "start_date": body.get("start_date"),
                "deadline": body.get("deadline"),
                "assignee_id": body.get("assignee_id"),
                "closure": {},
                "created_by": user_id,
                "updated_by": user_id,
                "archived": False,
                "created_at": now,
                "updated_at": now,
            }
        )
        await self.db.insert_documents(self.collection, [doc])
        # The access check above and this insert are not one atomic step. An
        # `archive_case` running alongside flips the Case and then drains its Tasks;
        # an insert that passes the check before the flip and lands after the drain
        # leaves a live Task on an archived Case — showing in the org-wide "my work"
        # list pointing at a 404, and blocking archive_state with a count nobody can
        # act on. Checked after the fact and undone rather than locked: losing this
        # race takes a few milliseconds of overlap, and a lock on every create is a
        # steep price for it. Deleted outright, not archived — no event has been
        # logged yet, so as far as the record is concerned the Task never existed.
        if not await self._cases().is_active(org_id, case_id):
            await self.db.mongo_handler.db[self.collection].delete_one({"_id": task_id})
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND, detail="Case not found"
            )
        # Logged after the write succeeds, never before.
        await self._log(doc, EventType.task_created, user_id)
        logger.info(f"Created task {task_id} on case {case_id}")
        return doc

    async def list_tasks(
        self,
        org_id: str,
        user_id: str,
        *,
        case_id: str | None = None,
        assignee_id: str | None = None,
        state_id: str | None = None,
        limit: int = 100,
        skip: int = 0,
    ) -> list[dict[str, Any]]:
        await self._orgs().assert_org_member(org_id, user_id)

        query: dict[str, Any] = {"org_id": org_id, **_ACTIVE_ONLY}
        if case_id:
            query["case_id"] = case_id
        if assignee_id:
            query["assignee_id"] = assignee_id
        if state_id:
            query["state_id"] = state_id
        return await self.db.find_documents(
            self.collection, query, limit=limit, skip=skip
        )

    async def get_task(self, org_id: str, task_id: str, user_id: str) -> dict[str, Any]:
        await self._orgs().assert_org_member(org_id, user_id)
        return await self._load_task(org_id, task_id)

    async def assert_task_in_case(
        self, org_id: str, case_id: str, task_id: str
    ) -> dict[str, Any]:
        """Confirm a Task belongs to a Case the caller has already been cleared for.

        No membership check: the only caller reached its Case through
        ``assert_project_access``, which already proved it. Going through
        ``get_task`` instead would re-run that same lookup for nothing.
        """
        task = await self._load_task(org_id, task_id)
        if task.get("case_id") != case_id:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Task does not belong to this Case",
            )
        return task

    async def update_task(
        self, org_id: str, task_id: str, user_id: str, updates: dict[str, Any]
    ) -> dict[str, Any]:
        task = await self.get_task(org_id, task_id, user_id)
        await self._assert_can_edit(org_id, task, user_id)

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
                # See CaseService.update_case — an explicit null must not fall
                # through to the unvalidated arm, and an unchanged value must not be
                # validated at all or a closed Task becomes uneditable.
                if not value:
                    raise HTTPException(
                        status_code=status.HTTP_400_BAD_REQUEST,
                        detail="A Task always sits in a workflow state",
                    )
                if value != task.get("state_id"):
                    # See CaseService.update_case — moving off the closed column
                    # would be one-way, so a closed Task is frozen until reopened.
                    if is_closed(task):
                        raise HTTPException(
                            status_code=status.HTTP_409_CONFLICT,
                            detail=(
                                "A closed Task stays on the closed column. Reopen it "
                                "first if its work is not finished after all."
                            ),
                        )
                    state = await self._states().assert_state_usable(
                        org_id, value, StateAppliesTo.task
                    )
                    set_fields["state_id"] = str(state["_id"])
            elif key == "assignee_id":
                await self._assert_assignee_is_member(org_id, value)
                set_fields["assignee_id"] = value
            else:
                set_fields[key] = value

        row = await self._apply(task, set_fields, user_id)
        await self._logs().record_changes(
            kind="task",
            previous=task,
            set_fields=set_fields,
            actor_id=user_id,
            case_id=task["case_id"],
            task_id=str(task["_id"]),
        )
        return row

    async def archive_task(
        self, org_id: str, task_id: str, user_id: str
    ) -> dict[str, Any]:
        await self._orgs().assert_org_admin(org_id, user_id)
        task = await self._load_task(org_id, task_id)
        row = await self._apply(task, {"archived": True}, user_id)
        await self._log(task, EventType.task_archived, user_id)
        return row

    async def reopen(
        self,
        org_id: str,
        task_id: str,
        user_id: str,
        state_id: str | None = None,
    ) -> dict[str, Any]:
        """Undo a Task's closure. Admin only — see CaseService.reopen.

        `state_id` is where the Task lands. The board sends the column the card
        was dragged onto: a closed Task is frozen against ordinary state moves,
        so without it an admin dragging one back to "In progress" needs a reopen
        onto the draft column followed by a second move, which records a state
        change through a column nobody asked for and leaves the Task stranded in
        draft if the second call fails. Omitted, it still lands on draft — the
        behaviour every existing caller expects.
        """
        await self._orgs().assert_org_admin(org_id, user_id)
        task = await self._load_task(org_id, task_id)

        closure = task.get("closure") or {}
        if not closure.get("approved_at"):
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT, detail="Task is not closed"
            )

        set_fields: dict[str, Any] = {"closure": {}}
        if state_id:
            # The ordinary validator, so a reopen cannot reach a closed column,
            # the Case board, or another organization's states either.
            state = await self._states().assert_state_usable(
                org_id, state_id, StateAppliesTo.task
            )
            set_fields["state_id"] = str(state["_id"])
        else:
            draft = await self._states().default_state_for(
                org_id, StateAppliesTo.task, StateCategory.draft
            )
            if draft:
                set_fields["state_id"] = str(draft["_id"])
        row = await self._apply(task, set_fields, user_id)
        await self._log(
            task,
            EventType.closure_reopened,
            user_id,
            {"approved_by": closure.get("approved_by")},
        )
        # The closure event says the Task is open again; this says which column
        # it opened onto, which is what the board and the timeline both read.
        await self._logs().record_changes(
            kind="task",
            previous=task,
            set_fields={"state_id": set_fields.get("state_id")}
            if set_fields.get("state_id")
            else {},
            actor_id=user_id,
            case_id=task["case_id"],
            task_id=str(task["_id"]),
        )
        return row

    async def archive_for_case(self, org_id: str, case_id: str, user_id: str) -> int:
        """Archive every live Task on a Case. Called only by CaseService.archive_case.

        No permission check of its own: the caller has already proved the actor is an
        admin of this organization, and there is no route onto this method.

        Each Task is claimed with its own conditional write, and logged only if that
        write is the one that archived it. A bulk `update_many` plus a loop over the
        rows read beforehand would be shorter, but the read is a stale snapshot: an
        admin archiving a Task individually in that window would get a second,
        misattributed `task.archived` event blaming the Case cascade for something it
        did not do. An evidence log may miss nothing, and may also invent nothing.

        Batched rather than read-all-then-write, so a Case with any number of Tasks
        is covered. Each pass archives the rows it read, and a row archived by
        somebody else also leaves the filter, so the loop always makes progress.
        """
        query = {"org_id": org_id, "case_id": case_id, **_ACTIVE_ONLY}
        tasks = self.db.mongo_handler.db[self.collection]
        archived = 0

        while True:
            batch = await self.db.find_documents(self.collection, query, limit=200)
            if not batch:
                return archived

            for task in batch:
                result = await tasks.update_one(
                    {"_id": task["_id"], **_ACTIVE_ONLY},
                    {
                        "$set": {
                            "archived": True,
                            "updated_by": user_id,
                            "updated_at": datetime.now(timezone.utc),
                        }
                    },
                )
                # Somebody else archived it first; it is their event to own.
                if result.modified_count:
                    archived += 1
                    await self._log(
                        task,
                        EventType.task_archived,
                        user_id,
                        {"cascade": "case.archived"},
                    )

    async def request_closure(
        self, org_id: str, task_id: str, user_id: str
    ) -> dict[str, Any]:
        task = await self.get_task(org_id, task_id, user_id)
        await self._assert_can_edit(org_id, task, user_id)
        # Scoped to this Task: a sibling's draft must not block it.
        await self._drafts().assert_no_active_draft(
            org_id, task["case_id"], task_id=task_id
        )

        if is_closed(task):
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT, detail="Task is already closed"
            )
        closure = {
            **(task.get("closure") or {}),
            "requested_by": user_id,
            "requested_at": datetime.now(timezone.utc),
        }
        row = await self._apply(task, {"closure": closure}, user_id)
        await self._log(task, EventType.closure_requested, user_id)
        return row

    async def approve_closure(
        self, org_id: str, task_id: str, user_id: str
    ) -> dict[str, Any]:
        """Single writer of the closed lifecycle: closure and state move together."""
        await self._orgs().assert_org_admin(org_id, user_id)
        task = await self._load_task(org_id, task_id)
        # Scoped to this Task: a sibling's draft must not block it.
        await self._drafts().assert_no_active_draft(
            org_id, task["case_id"], task_id=task_id
        )

        closure = dict(task.get("closure") or {})
        if not closure.get("requested_at"):
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="Closure has not been requested for this Task",
            )
        if closure.get("approved_at"):
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT, detail="Task is already closed"
            )

        now = datetime.now(timezone.utc)
        closure["approved_by"] = user_id
        closure["approved_at"] = now

        set_fields: dict[str, Any] = {"closure": closure}
        closed_state = await self._states().default_state_for(
            org_id, StateAppliesTo.task, StateCategory.closed
        )
        if closed_state:
            set_fields["state_id"] = str(closed_state["_id"])
        row = await self._apply(task, set_fields, user_id)
        await self._log(
            task,
            EventType.closure_approved,
            user_id,
            {"requested_by": closure.get("requested_by")},
        )
        return row

    async def count_using_state(self, org_id: str, state_id: str) -> int:
        """How many live Tasks sit in a column — see CaseService.count_using_state."""
        return await self.db.mongo_handler.db[self.collection].count_documents(
            {"org_id": org_id, "state_id": state_id, **_ACTIVE_ONLY}
        )

    async def set_state(
        self, org_id: str, task_id: str, state_id: str, user_id: str
    ) -> dict[str, Any]:
        """Move a Task to a state, for callers outside this service.

        See CaseService.set_state — same reason, same contract: no permission
        check, because the caller has already authorized the triggering action,
        but the closure freeze is enforced for every caller.
        """
        task = await self._load_task(org_id, task_id)
        if is_closed(task):
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=(
                    "A closed Task stays on the closed column. Reopen it "
                    "first if its work is not finished after all."
                ),
            )
        return await self._apply(task, {"state_id": state_id}, user_id)

    async def _apply(
        self, task: dict[str, Any], set_fields: dict[str, Any], user_id: str
    ) -> dict[str, Any]:
        row = dict(task)
        if not set_fields:
            return row
        set_fields = {
            **set_fields,
            "updated_by": user_id,
            "updated_at": datetime.now(timezone.utc),
        }
        await self.db.update_documents(
            self.collection,
            {"_id": task["_id"], "org_id": task["org_id"]},
            {"$set": clean_for_mongodb(set_fields)},
        )
        row.update(set_fields)
        return row
