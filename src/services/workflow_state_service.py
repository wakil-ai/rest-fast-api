"""Workflow states: the board columns an organization defines for Cases and Tasks."""

from datetime import datetime, timezone
from typing import Any

from fastapi import HTTPException, status
from pymongo.errors import DuplicateKeyError

from core.config import settings
from core.dependencies import get_db_manager, get_organization_service
from core.logger import logger
from models.workflow_states import StateAppliesTo, StateCategory
from utils.user_management import clean_for_mongodb, generate_short_id

# Reads exclude soft-deleted docs, mirroring organization_service._ACTIVE_ONLY.
_ACTIVE_ONLY: dict[str, Any] = {"archived": {"$ne": True}}

# Seeded into every organization, for both boards. English labels on purpose: the
# organization renames them, which is the entire reason states are rows and not an
# enum.
DEFAULT_STATES: tuple[tuple[str, StateCategory], ...] = (
    ("Draft", StateCategory.draft),
    ("In Progress", StateCategory.in_progress),
    ("In Review", StateCategory.in_review),
    ("Returned for revision", StateCategory.returned),
    ("Done", StateCategory.closed),
)


class WorkflowStateService:
    """Board configuration. Admins write it; every member reads it."""

    def __init__(self) -> None:
        self.db = get_db_manager()
        self.collection = settings.WORKFLOW_STATES_COLLECTION
        self.prefix = "wfst-"

    async def ensure_indexes(self) -> None:
        """Create the collection's indexes. Call from a startup hook, not __init__.

        The sibling services schedule this with ``loop.create_task`` in their
        constructor, which never fires in production — services are built at module
        import when no event loop is running — and which attaches a task to whatever
        loop happens to be live when it *is* built, outliving it. Nothing here
        depends on the indexes: seeding is upsert-based and correct without them,
        and the unique index only hardens the concurrent case.
        """
        await self.db.create_collection(self.collection)
        states = self.db.mongo_handler.db[self.collection]
        # Partial, so archiving a state frees its name for reuse.
        #
        # `{"archived": False}`, not the `{"$ne": True}` the reads use: MongoDB
        # rejects $ne in a partialFilterExpression ("Expression not supported in
        # partial index"), and rejecting means the index is never created at all —
        # silently, since nothing else depends on it existing. Every row this
        # service writes sets `archived` explicitly, so an equality match covers
        # exactly the live ones.
        await states.create_index(
            [("org_id", 1), ("applies_to", 1), ("name", 1)],
            unique=True,
            partialFilterExpression={"archived": False},
            name="org_applies_name_unique",
        )
        await states.create_index(
            [("org_id", 1), ("applies_to", 1), ("order", 1)], name="org_board_order"
        )

    async def ensure_default_states(self, org_id: str) -> None:
        """Seed the two default boards. Safe to call any number of times.

        One upsert per state rather than a bulk insert that swallows duplicate-key
        errors: nothing calls ``ensure_indexes``, and the sibling services that do
        schedule index creation never actually run it, so the unique index cannot be
        assumed to exist. Without it there is no duplicate-key error to catch, and an
        insert-based seed would append another ten rows on every call. ``$setOnInsert`` also means re-running this
        never overwrites a state the organization has since renamed or reordered.
        """
        now = datetime.now(timezone.utc)
        coll = self.db.mongo_handler.db[self.collection]
        for applies_to in StateAppliesTo:
            for order, (name, category) in enumerate(DEFAULT_STATES, start=1):
                try:
                    await coll.update_one(
                        {
                            "org_id": org_id,
                            "applies_to": applies_to.value,
                            "name": name,
                        },
                        {
                            "$setOnInsert": clean_for_mongodb(
                                {
                                    "_id": generate_short_id(
                                        prefix=self.prefix, type="uuid7"
                                    ),
                                    "org_id": org_id,
                                    "applies_to": applies_to.value,
                                    "name": name,
                                    "category": category.value,
                                    "order": order,
                                    "is_system": True,
                                    "archived": False,
                                    "created_at": now,
                                    "updated_at": now,
                                }
                            )
                        },
                        upsert=True,
                    )
                except DuplicateKeyError:
                    # Seeding is lazy, so two first-requests on a fresh org can race
                    # here — a board load and the first Case creation, say. An upsert
                    # is not atomic against the unique index, so the loser sees
                    # E11000. The other caller wrote the state we wanted; carry on.
                    continue
        logger.info(f"Ensured default workflow states for organization {org_id}")

    async def _load_state_row(self, org_id: str, state_id: str) -> dict[str, Any]:
        rows = await self.db.find_documents(
            self.collection,
            {"_id": state_id, "org_id": org_id, **_ACTIVE_ONLY},
            limit=1,
        )
        if not rows:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND, detail="Workflow state not found"
            )
        return rows[0]

    async def list_states(
        self,
        org_id: str,
        user_id: str,
        applies_to: StateAppliesTo | None = None,
    ) -> list[dict[str, Any]]:
        await get_organization_service().assert_org_member(org_id, user_id)

        rows = await self._read_board(org_id, applies_to)
        if not rows:
            # Organizations created before this feature existed have no board.
            # Seeding on an empty read heals them without a migration script.
            await self.ensure_default_states(org_id)
            rows = await self._read_board(org_id, applies_to)
        return rows

    async def _read_board(
        self, org_id: str, applies_to: StateAppliesTo | None
    ) -> list[dict[str, Any]]:
        query: dict[str, Any] = {"org_id": org_id, **_ACTIVE_ONLY}
        if applies_to is not None:
            query["applies_to"] = applies_to.value
        rows = await self.db.find_documents(self.collection, query, limit=200)
        return sorted(rows, key=lambda r: (r.get("applies_to", ""), r.get("order", 0)))

    async def assert_state_usable(
        self, org_id: str, state_id: str, applies_to: StateAppliesTo
    ) -> dict[str, Any]:
        """The single validator every member-supplied state_id goes through.

        Without the org and applies_to checks a member could point their Case at
        another organization's column, or at one belonging to the Task board.

        A closed-category column is refused here rather than in each caller. Closure
        is admin-approved, but `state_id` is an ordinary editable field, so without
        this an assignee could PATCH straight onto "Done": the Case would read as
        closed to every dashboard and reminder — those key on `category` — while
        `status` stayed active and no admin ever signed it off. The approval path
        reaches the closed column through ``default_state_for``, not through here,
        so it remains the only writer of the closed lifecycle.
        """
        state = await self._load_state_row(org_id, state_id)
        if state.get("applies_to") != applies_to.value:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Workflow state does not apply to a {applies_to.value}",
            )
        if state.get("category") == StateCategory.closed.value:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=(
                    "Work reaches a closed state by having its closure approved, "
                    "not by moving it there directly"
                ),
            )
        return state

    async def default_state_for(
        self, org_id: str, applies_to: StateAppliesTo, category: StateCategory
    ) -> dict[str, Any] | None:
        """The board's first state in a category — where new and closed items land.

        Returns the lowest-ordered match so a board with several closed columns
        behaves predictably. Seeds an empty board first, so callers never have to
        remember to; seeding unconditionally would put twenty writes behind every
        Case and Task creation.
        """
        if not await self._read_board(org_id, applies_to):
            await self.ensure_default_states(org_id)

        rows = await self.db.find_documents(
            self.collection,
            {
                "org_id": org_id,
                "applies_to": applies_to.value,
                "category": category.value,
                **_ACTIVE_ONLY,
            },
            limit=50,
        )
        if not rows:
            return None
        return min(rows, key=lambda r: r.get("order", 0))

    async def create_state(
        self, org_id: str, user_id: str, body: dict[str, Any]
    ) -> dict[str, Any]:
        await get_organization_service().assert_org_admin(org_id, user_id)

        name = (body.get("name") or "").strip()
        if not name:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST, detail="Name is required"
            )
        applies_to = StateAppliesTo(body["applies_to"])
        await self._assert_name_free(org_id, applies_to, name)

        order = body.get("order")
        if order is None:
            existing = await self._read_board(org_id, applies_to)
            order = max((r.get("order", 0) for r in existing), default=0) + 1

        now = datetime.now(timezone.utc)
        doc = clean_for_mongodb(
            {
                "_id": generate_short_id(prefix=self.prefix, type="uuid7"),
                "org_id": org_id,
                "applies_to": applies_to.value,
                "name": name,
                "category": StateCategory(body["category"]).value,
                "order": int(order),
                "is_system": False,
                "archived": False,
                "created_at": now,
                "updated_at": now,
            }
        )
        await self.db.insert_documents(self.collection, [doc])
        return doc

    async def update_state(
        self, org_id: str, state_id: str, user_id: str, updates: dict[str, Any]
    ) -> dict[str, Any]:
        """Rename or reposition. System states are renamable — only undeletable."""
        await get_organization_service().assert_org_admin(org_id, user_id)
        state = await self._load_state_row(org_id, state_id)

        set_fields: dict[str, Any] = {}
        if "name" in updates:
            name = (updates["name"] or "").strip()
            if not name:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST, detail="Name is required"
                )
            if name != state.get("name"):
                await self._assert_name_free(
                    org_id, StateAppliesTo(state["applies_to"]), name
                )
            set_fields["name"] = name
        if "order" in updates and updates["order"] is not None:
            set_fields["order"] = int(updates["order"])

        row = dict(state)
        if set_fields:
            set_fields["updated_at"] = datetime.now(timezone.utc)
            await self.db.update_documents(
                self.collection,
                {"_id": state_id, "org_id": org_id},
                {"$set": clean_for_mongodb(set_fields)},
            )
            row.update(set_fields)
        return row

    async def archive_state(
        self, org_id: str, state_id: str, user_id: str
    ) -> dict[str, Any]:
        await get_organization_service().assert_org_admin(org_id, user_id)
        state = await self._load_state_row(org_id, state_id)

        if state.get("is_system"):
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=(
                    "Default states cannot be deleted, only renamed. Removing the "
                    "closed state would leave nowhere to close work into."
                ),
            )

        in_use = await self._count_in_use(org_id, state_id, state.get("applies_to"))
        if in_use:
            noun = (
                "Case"
                if state.get("applies_to") == StateAppliesTo.case.value
                else "Task"
            )
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=(
                    f"{in_use} {noun}(s) are in this state. Move them to another "
                    "state before deleting it."
                ),
            )

        now = datetime.now(timezone.utc)
        await self.db.update_documents(
            self.collection,
            {"_id": state_id, "org_id": org_id},
            {"$set": {"archived": True, "archived_at": now, "updated_at": now}},
        )

        # Re-count after the write, not only before it. Between the first count and
        # this line the column is still live, so a Case or Task can be PATCHed into
        # it — assert_state_usable would read it as usable. That item would then sit
        # on an archived state: _load_state_row 404s it, so it drops off every board
        # and out of count_using_state, which is exactly what the guard exists to
        # prevent. Archiving first and undoing is what makes the check decisive:
        # once the state is archived nothing new can land on it.
        slipped_in = await self._count_in_use(org_id, state_id, state.get("applies_to"))
        if slipped_in:
            await self.db.update_documents(
                self.collection,
                {"_id": state_id, "org_id": org_id},
                {
                    "$set": {"archived": False, "updated_at": now},
                    "$unset": {"archived_at": ""},
                },
            )
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=(
                    "Work moved into this state while it was being deleted. "
                    "Move it out and try again."
                ),
            )

        row = dict(state)
        row["archived"] = True
        return row

    async def _count_in_use(
        self, org_id: str, state_id: str, applies_to: str | None
    ) -> int:
        """Live Cases or Tasks sitting in a column.

        Refusing beats auto-migrating: silently moving a customer's work between
        board columns is worse than an error telling them to move it themselves.
        Imported here rather than at module scope — both services import this one.
        """
        if applies_to == StateAppliesTo.case.value:
            from core.dependencies import get_case_service

            return await get_case_service().count_using_state(org_id, state_id)

        from core.dependencies import get_task_service

        return await get_task_service().count_using_state(org_id, state_id)

    async def _assert_name_free(
        self, org_id: str, applies_to: StateAppliesTo, name: str
    ) -> None:
        clash = await self.db.find_documents(
            self.collection,
            {
                "org_id": org_id,
                "applies_to": applies_to.value,
                "name": name,
                **_ACTIVE_ONLY,
            },
            limit=1,
        )
        if clash:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=f"A {applies_to.value} state named '{name}' already exists",
            )
