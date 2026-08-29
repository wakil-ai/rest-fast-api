"""The Non-Substitution Gate: AI output is a draft until a human says otherwise.

ZRU-1115 evidence lives in these rows, so the tests lean hard on the negative
paths — what must NOT happen when two people act at once, when a client
disconnects, and when a generation outlives its own reclaim window.
"""

import asyncio
from datetime import datetime, timedelta, timezone
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import HTTPException
from pymongo.errors import DuplicateKeyError

from core.config import settings
from core.exceptions import QueryTooLongException
from models.drafts import DraftStatus
from models.workflow_states import StateAppliesTo, StateCategory
from services.chat_service import ChatService
from services.draft_service import DraftService


@pytest.fixture
def collection() -> MagicMock:
    col = MagicMock()
    col.create_index = AsyncMock()
    col.insert_one = AsyncMock()
    col.update_one = AsyncMock()
    col.find_one = AsyncMock()
    return col


@pytest.fixture
def service(collection: MagicMock) -> DraftService:
    svc = DraftService.__new__(DraftService)
    svc._collection = lambda: collection  # type: ignore[method-assign]
    return svc


async def test_ensure_indexes_creates_the_unique_slot_index(
    service: DraftService, collection: MagicMock
) -> None:
    await service.ensure_indexes()

    slot = [
        call
        for call in collection.create_index.await_args_list
        if call.kwargs.get("name") == "uniq_active_draft"
    ]
    assert len(slot) == 1
    assert slot[0].kwargs["unique"] is True
    # Equality only: $ne and $in are rejected by partialFilterExpression.
    assert slot[0].kwargs["partialFilterExpression"] == {"slot_active": True}
    assert slot[0].args[0] == [("org_id", 1), ("case_id", 1), ("task_id", 1)]


async def test_both_listing_queries_have_a_covering_index(
    service: DraftService, collection: MagicMock
) -> None:
    """list_for_task filters on task_id without case_id, so the case index does
    not serve it — a missing task index is a full collection scan, not a slow one."""
    await service.ensure_indexes()

    keys = [call.args[0] for call in collection.create_index.await_args_list]
    assert [("org_id", 1), ("case_id", 1), ("created_at", -1)] in keys
    assert [("org_id", 1), ("task_id", 1), ("created_at", -1)] in keys


async def test_no_index_is_built_on_status(
    service: DraftService, collection: MagicMock
) -> None:
    """Nothing queries this collection by status. An index that matches no query
    is write cost for nothing."""
    await service.ensure_indexes()

    keys = [call.args[0] for call in collection.create_index.await_args_list]
    assert not any("status" in dict(key) for key in keys)


def test_gate_event_types_exist() -> None:
    from models.activity_logs import EventType

    assert EventType.ai_request_submitted.value == "ai.request_submitted"
    assert EventType.ai_draft_generated.value == "ai.draft_generated"
    assert EventType.ai_draft_edited.value == "ai.draft_edited"
    assert EventType.ai_draft_approved.value == "ai.draft_approved"
    assert EventType.ai_draft_rejected.value == "ai.draft_rejected"
    assert EventType.ai_generation_failed.value == "ai.generation_failed"
    assert EventType.ai_request_edited.value == "ai.request_edited"


# --- the reviewed request ---------------------------------------------------


def _prepare_service(service: DraftService, holder: dict[str, Any]) -> DraftService:
    """A service whose Case lookup returns `holder` and nothing else is wired."""
    cases = MagicMock()
    cases.assert_case_access = AsyncMock(return_value=holder)
    service._cases = lambda: cases  # type: ignore[method-assign]
    return service


CASE_HOLDER: dict[str, Any] = {
    "_id": "proj-1",
    "owner_id": "owner",
    "title": "Contract dispute",
    "objective": "Recover the deposit",
    "description": "Counterparty stopped replying",
    "case_type": "court",
}


async def test_prepare_returns_the_composed_request_and_its_hash(
    service: DraftService,
) -> None:
    _prepare_service(service, CASE_HOLDER)

    out = await service.prepare_delegation(
        org_id="org-1", case_id="proj-1", task_id=None, user_id="owner"
    )

    assert "Contract dispute" in out["query"]
    assert "Recover the deposit" in out["query"]
    assert out["base_hash"] == service._hash_query(out["query"])
    assert out["closed"] is False


async def test_prepare_defaults_the_role_from_the_record(
    service: DraftService,
) -> None:
    _prepare_service(service, CASE_HOLDER)

    out = await service.prepare_delegation(
        org_id="org-1", case_id="proj-1", task_id=None, user_id="owner"
    )

    assert out["assistant"] == "court"
    assert [r["key"] for r in out["roles"]] == ["main", "deepresearch", "court", "tax"]


async def test_prepare_falls_back_to_general_for_an_unknown_role(
    service: DraftService,
) -> None:
    """A stale or non-public name on the record must not reach the model."""
    _prepare_service(service, {**CASE_HOLDER, "case_type": "administrative_court"})

    out = await service.prepare_delegation(
        org_id="org-1", case_id="proj-1", task_id=None, user_id="owner"
    )

    assert out["assistant"] == "main"


async def test_prepare_reserves_no_slot_and_charges_nothing(
    service: DraftService, collection: MagicMock
) -> None:
    """Opening the review step and walking away must leave no trace."""
    _prepare_service(service, CASE_HOLDER)

    await service.prepare_delegation(
        org_id="org-1", case_id="proj-1", task_id=None, user_id="owner"
    )

    assert collection.insert_one.await_count == 0
    assert collection.update_one.await_count == 0


async def test_prepare_carries_a_previous_draft_into_the_request(
    service: DraftService, collection: MagicMock
) -> None:
    """Regeneration is a revision: the earlier text comes back with the request."""
    _prepare_service(service, CASE_HOLDER)
    collection.find_one.return_value = draft_row(content="the earlier answer")

    out = await service.prepare_delegation(
        org_id="org-1",
        case_id="proj-1",
        task_id=None,
        user_id="owner",
        previous_draft_id="draft-1",
    )

    assert "the earlier answer" in out["query"]


async def test_prepare_refuses_a_draft_from_other_work(
    service: DraftService, collection: MagicMock
) -> None:
    _prepare_service(service, CASE_HOLDER)
    collection.find_one.return_value = draft_row(case_id="proj-OTHER")

    with pytest.raises(HTTPException) as exc:
        await service.prepare_delegation(
            org_id="org-1",
            case_id="proj-1",
            task_id=None,
            user_id="owner",
            previous_draft_id="draft-1",
        )

    assert exc.value.status_code == 400


def test_an_untouched_request_hashes_back_to_its_own_fingerprint(
    service: DraftService,
) -> None:
    """Whitespace-only differences must not read as a human edit."""
    query = "Title: A\n\nObjective: B"

    assert service._hash_query(query) == service._hash_query(f"  {query}  ")


async def test_reserve_slot_records_both_versions_of_the_request(
    service: DraftService, collection: MagicMock
) -> None:
    collection.find_one.return_value = None

    await service.reserve_slot(
        **RESERVE,
        query_base="Title: A",
        query_final="Title: A, rewritten",
        query_edited=True,
    )

    doc = collection.insert_one.await_args.args[0]
    assert doc["query_base"] == "Title: A"
    assert doc["query_final"] == "Title: A, rewritten"
    assert doc["query_edited"] is True


# --- slot reservation -------------------------------------------------------

RESERVE: dict[str, Any] = {
    "org_id": "org-1",
    "case_id": "proj-1",
    "task_id": None,
    "session_id": "ses-1",
    "assistant": "tax",
    "user_id": "member-a",
}


async def test_reserve_slot_inserts_generating_row(
    service: DraftService, collection: MagicMock
) -> None:
    collection.find_one.return_value = None

    draft_id = await service.reserve_slot(**RESERVE)

    doc = collection.insert_one.await_args.args[0]
    assert draft_id.startswith("draft-")
    assert doc["status"] == DraftStatus.generating.value
    assert doc["slot_active"] is True
    assert doc["content"] == ""
    assert doc["version"] == 1
    assert doc["chain_id"] == draft_id
    assert doc["parent_draft_id"] is None
    assert doc["source"] == "agent"


async def test_reserve_slot_uses_insert_one_not_insert_many(
    service: DraftService, collection: MagicMock
) -> None:
    """insert_many raises BulkWriteError, which would never match our handler."""
    collection.find_one.return_value = None

    await service.reserve_slot(**RESERVE)

    assert collection.insert_one.await_count == 1


async def test_reserve_slot_maps_duplicate_key_to_409(
    service: DraftService, collection: MagicMock
) -> None:
    collection.find_one.return_value = None
    collection.insert_one.side_effect = DuplicateKeyError("dup")

    with pytest.raises(HTTPException) as exc:
        await service.reserve_slot(**RESERVE)

    assert exc.value.status_code == 409


async def test_reserve_slot_reclaims_abandoned_generating_row(
    service: DraftService, collection: MagicMock
) -> None:
    stale_at = datetime.now(timezone.utc) - timedelta(
        seconds=service.RECLAIM_AFTER_SECONDS + 60
    )
    collection.find_one.return_value = {
        "_id": "draft-stale",
        "status": DraftStatus.generating.value,
        "created_at": stale_at,
    }
    collection.update_one.return_value = MagicMock(matched_count=1)

    await service.reserve_slot(**RESERVE)

    # The reclaim is conditional — read-then-write would reopen the TOCTOU.
    predicate = collection.update_one.await_args.args[0]
    assert predicate["_id"] == "draft-stale"
    assert predicate["status"] == DraftStatus.generating.value
    assert predicate["slot_active"] is True
    assert collection.insert_one.await_count == 1


async def test_a_lost_reclaim_race_is_not_logged_as_a_reclaim(
    service: DraftService,
    collection: MagicMock,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The update is conditional, so it can match nothing when another caller
    reclaimed the same row first. Logging regardless would report a reclaim that
    this process did not perform — and that log is the only trace an abandoned
    generation leaves."""
    stale_at = datetime.now(timezone.utc) - timedelta(
        seconds=service.RECLAIM_AFTER_SECONDS + 60
    )
    collection.find_one.return_value = {
        "_id": "draft-stale",
        "status": DraftStatus.generating.value,
        "created_at": stale_at,
    }
    collection.update_one.return_value = MagicMock(modified_count=0)
    # The service logger, not caplog: core.logger does not propagate to the root
    # handler, so a caplog assertion here passes whatever the code does.
    log = MagicMock()
    monkeypatch.setattr("services.draft_service.logger", log)

    await service.reserve_slot(**RESERVE)

    log.warning.assert_not_called()


async def test_reserve_slot_does_not_reclaim_a_live_generation(
    service: DraftService, collection: MagicMock
) -> None:
    collection.find_one.return_value = {
        "_id": "draft-live",
        "status": DraftStatus.generating.value,
        "created_at": datetime.now(timezone.utc),
    }
    collection.insert_one.side_effect = DuplicateKeyError("dup")

    with pytest.raises(HTTPException) as exc:
        await service.reserve_slot(**RESERVE)

    assert exc.value.status_code == 409
    collection.update_one.assert_not_awaited()


async def test_reserve_slot_ignores_a_naive_created_at(
    service: DraftService, collection: MagicMock
) -> None:
    """Mongo hands back naive datetimes. Comparing one to an aware `now` raises
    TypeError, which would surface as a 500 on every delegation after a restart."""
    stale_naive = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(
        seconds=service.RECLAIM_AFTER_SECONDS + 60
    )
    collection.find_one.return_value = {
        "_id": "draft-stale",
        "status": DraftStatus.generating.value,
        "created_at": stale_naive,
    }
    collection.update_one.return_value = MagicMock(matched_count=1)

    await service.reserve_slot(**RESERVE)

    assert collection.update_one.await_args.args[0]["_id"] == "draft-stale"


async def test_release_slot_unsets_the_flag(
    service: DraftService, collection: MagicMock
) -> None:
    await service.release_slot("draft-1")

    update = collection.update_one.await_args.args[1]
    assert update["$set"]["status"] == DraftStatus.failed.value
    assert update["$unset"] == {"slot_active": ""}


# --- terminal write ---------------------------------------------------------


async def test_finalize_draft_moves_generating_to_pending(
    service: DraftService, collection: MagicMock
) -> None:
    collection.update_one.return_value = MagicMock(matched_count=1)

    landed = await service.finalize_draft("draft-1", "the draft", "msg-1")

    assert landed is True
    predicate, update = collection.update_one.await_args.args[:2]
    assert predicate == {"_id": "draft-1", "status": DraftStatus.generating.value}
    assert update["$set"]["status"] == DraftStatus.pending.value
    assert update["$set"]["content"] == "the draft"
    assert update["$set"]["message_id"] == "msg-1"


async def test_finalize_draft_refuses_to_resurrect_a_reclaimed_row(
    service: DraftService, collection: MagicMock
) -> None:
    """The zombie guard: a reclaimed row must not flip back to pending."""
    collection.update_one.return_value = MagicMock(matched_count=0)

    landed = await service.finalize_draft("draft-1", "late answer", "msg-1")

    assert landed is False


async def test_finalize_draft_retries_transient_failures(
    service: DraftService, collection: MagicMock
) -> None:
    collection.update_one.side_effect = [
        RuntimeError("mongo blip"),
        RuntimeError("mongo blip"),
        MagicMock(matched_count=1),
    ]

    landed = await service.finalize_draft("draft-1", "the draft", "msg-1")

    assert landed is True
    assert collection.update_one.await_count == 3


async def test_finalize_draft_gives_up_after_three_attempts(
    service: DraftService, collection: MagicMock
) -> None:
    collection.update_one.side_effect = RuntimeError("mongo down")

    with pytest.raises(RuntimeError):
        await service.finalize_draft("draft-1", "the draft", "msg-1")

    assert collection.update_one.await_count == 3


async def test_detached_completes_despite_repeated_cancellation() -> None:
    """anyio cancel scopes re-raise at every checkpoint, so a bare
    `await asyncio.shield(...)` is not enough — detached() must swallow the
    CancelledError delivered into the awaiting frame as well.

    The write body has real checkpoints on purpose: a body with no `await`
    finishes on its first scheduler step, before any cancellation can arrive,
    and the test would pass against a detached() that did nothing at all.
    """
    from services.draft_service import detached

    landed = []

    async def write() -> None:
        for _ in range(5):
            await asyncio.sleep(0)
        landed.append(True)

    async def victim() -> None:
        await detached(write())

    # The cancellation targets a child task, not this one. Cancelling the test
    # task instead — as an earlier draft of this test did — means the cancels that
    # detached() does not swallow land on the assertions below, which no
    # implementation of detached() can prevent.
    task = asyncio.ensure_future(victim())
    for _ in range(5):
        await asyncio.sleep(0)
        task.cancel()

    # detached() returns as soon as the cancellation is swallowed, which can be
    # before the shielded task finishes — shielding protects the write, it does
    # not join it. Give the orphan its remaining steps.
    await asyncio.sleep(0.05)
    assert landed == [True]


async def test_the_caller_is_not_cancelled_by_a_disconnect() -> None:
    """The point of the wrapper: no CancelledError escapes to the caller."""
    from services.draft_service import detached

    async def write() -> None:
        await asyncio.sleep(0)

    async def cancel_next_tick(task: Any) -> None:
        await asyncio.sleep(0)
        task.cancel()

    async def run() -> str:
        asyncio.ensure_future(cancel_next_tick(asyncio.current_task()))
        await detached(write())
        return "reached"

    assert await run() == "reached"


# --- decide -----------------------------------------------------------------


def draft_row(**overrides: Any) -> dict[str, Any]:
    row: dict[str, Any] = {
        "_id": "draft-1",
        "org_id": "org-1",
        "case_id": "proj-1",
        "task_id": None,
        "session_id": "ses-1",
        "chain_id": "draft-1",
        "version": 1,
        "status": DraftStatus.pending.value,
        "slot_active": True,
        "content": "draft text",
        "assistant": "tax",
        "created_by": "member-a",
    }
    row.update(overrides)
    return row


@pytest.fixture
def decidable(service: DraftService, collection: MagicMock) -> tuple[Any, ...]:
    """A pending draft on a Case owned by `owner`, with no assignee."""
    collection.find_one.return_value = draft_row()
    collection.update_one.return_value = MagicMock(matched_count=1)

    cases = MagicMock()
    cases.assert_case_access = AsyncMock(
        return_value={"_id": "proj-1", "owner_id": "owner", "title": "Contract"}
    )
    # _load_case and _apply, not set_state — there is no public state setter, and
    # an unconfigured MagicMock attribute raises TypeError the moment it is
    # awaited, which _move_to_in_review's bare `except Exception` would swallow.
    # Every board-move test would then pass without moving anything.
    cases._load_case = AsyncMock(
        return_value={"_id": "proj-1", "owner_id": "owner", "title": "Contract"}
    )
    cases.set_state = AsyncMock(return_value={"_id": "proj-1"})
    service._cases = lambda: cases  # type: ignore[method-assign]

    states = MagicMock()
    states.default_state_for = AsyncMock(return_value={"_id": "wfst-review"})
    service._states = lambda: states  # type: ignore[method-assign]

    logs = MagicMock()
    logs.record = AsyncMock()
    service._logs = lambda: logs  # type: ignore[method-assign]

    orgs = MagicMock()
    orgs.assert_org_admin = AsyncMock()
    service._orgs = lambda: orgs  # type: ignore[method-assign]
    return service, collection, cases, states, logs, orgs


def _task_holder(service: DraftService) -> MagicMock:
    tasks = MagicMock()
    holder = {"_id": "task-1", "created_by": "owner", "title": "Draft reply"}
    tasks.get_task = AsyncMock(return_value=holder)
    tasks.set_state = AsyncMock(return_value={"_id": "task-1"})
    service._tasks = lambda: tasks  # type: ignore[method-assign]
    return tasks


async def test_approve_sets_decided_fields_and_frees_the_slot(decidable: tuple[Any, ...]) -> None:
    service, collection, *_ = decidable

    await service.approve("org-1", "draft-1", "owner")

    predicate, update = collection.update_one.await_args.args[:2]
    assert predicate["status"] == DraftStatus.pending.value
    # Belt and braces: a pending row not holding the slot is not decidable.
    assert predicate["slot_active"] is True
    assert update["$set"]["status"] == DraftStatus.approved.value
    assert update["$set"]["decided_by"] == "owner"
    assert update["$unset"] == {"slot_active": ""}


async def test_second_decision_returns_409(decidable: tuple[Any, ...]) -> None:
    service, collection, *_ = decidable
    collection.update_one.return_value = MagicMock(matched_count=0)

    with pytest.raises(HTTPException) as exc:
        await service.approve("org-1", "draft-1", "owner")

    assert exc.value.status_code == 409


async def test_generating_row_is_not_decidable(decidable: tuple[Any, ...]) -> None:
    service, collection, *_ = decidable
    collection.find_one.return_value = draft_row(status=DraftStatus.generating.value)

    with pytest.raises(HTTPException) as exc:
        await service.approve("org-1", "draft-1", "owner")

    assert exc.value.status_code == 409
    assert "still being generated" in exc.value.detail


async def test_unrelated_member_gets_403(decidable: tuple[Any, ...]) -> None:
    service, _, _, _, _, orgs = decidable
    orgs.assert_org_admin.side_effect = HTTPException(status_code=403, detail="no")

    with pytest.raises(HTTPException) as exc:
        await service.approve("org-1", "draft-1", "stranger")

    assert exc.value.status_code == 403


async def test_other_org_gets_404(decidable: tuple[Any, ...]) -> None:
    """404, never 403 — a 403 would confirm the row exists to a foreign org."""
    service, collection, *_ = decidable
    collection.find_one.return_value = None

    with pytest.raises(HTTPException) as exc:
        await service.approve("org-2", "draft-1", "owner")

    assert exc.value.status_code == 404


async def test_approve_moves_a_case_to_in_review(decidable: tuple[Any, ...]) -> None:
    service, _, cases, states, logs, _ = decidable

    result = await service.approve("org-1", "draft-1", "owner")

    states.default_state_for.assert_awaited_once_with(
        "org-1", StateAppliesTo.case, StateCategory.in_review
    )
    # The write itself, not just the lookup that precedes it.
    cases.set_state.assert_awaited_once_with("org-1", "proj-1", "wfst-review", "owner")
    assert logs.record.await_args.kwargs["payload"]["state_move"] == "moved"
    assert result["status"] == DraftStatus.approved.value


async def test_approve_moves_a_task_on_the_task_board(decidable: tuple[Any, ...]) -> None:
    service, collection, _, states, logs, _ = decidable
    collection.find_one.return_value = draft_row(task_id="task-1")
    tasks = _task_holder(service)

    await service.approve("org-1", "draft-1", "owner")

    states.default_state_for.assert_awaited_once_with(
        "org-1", StateAppliesTo.task, StateCategory.in_review
    )
    tasks.set_state.assert_awaited_once_with("org-1", "task-1", "wfst-review", "owner")
    assert logs.record.await_args.kwargs["payload"]["state_move"] == "moved"


async def test_a_task_draft_never_moves_the_parent_case(decidable: tuple[Any, ...]) -> None:
    """The row carries case_id for the closure guard's benefit; it must not make
    a Task decision drag the whole Case into review."""
    service, collection, cases, _, _, _ = decidable
    collection.find_one.return_value = draft_row(task_id="task-1")
    _task_holder(service)

    await service.approve("org-1", "draft-1", "owner")

    cases.set_state.assert_not_awaited()


async def test_approve_logs_skip_when_board_has_no_in_review(decidable: tuple[Any, ...]) -> None:
    service, _, _, states, logs, _ = decidable
    states.default_state_for.return_value = None

    await service.approve("org-1", "draft-1", "owner")

    payload = logs.record.await_args.kwargs["payload"]
    assert payload["state_move"] == "skipped_no_in_review_state"


async def test_failing_board_move_still_returns_the_approval(decidable: tuple[Any, ...]) -> None:
    service, _, cases, _, logs, _ = decidable
    # On set_state, the call the code actually makes. Pointing this at a method the
    # implementation never invokes would still produce "failed" — via a TypeError
    # from the unconfigured mock — and the test would pass while proving nothing.
    cases.set_state.side_effect = RuntimeError("board unavailable")

    result = await service.approve("org-1", "draft-1", "owner")

    assert result["status"] == DraftStatus.approved.value
    assert logs.record.await_args.kwargs["payload"]["state_move"] == "failed"


async def test_reject_is_terminal_and_frees_the_slot(decidable: tuple[Any, ...]) -> None:
    service, collection, *_ = decidable

    await service.reject("org-1", "draft-1", "owner")

    update = collection.update_one.await_args.args[1]
    assert update["$set"]["status"] == DraftStatus.rejected.value
    assert update["$unset"] == {"slot_active": ""}


async def test_reject_does_not_move_the_board(decidable: tuple[Any, ...]) -> None:
    """Only approval moves an item into review. A refusal leaves it where it was."""
    service, _, cases, states, _, _ = decidable

    await service.reject("org-1", "draft-1", "owner")

    states.default_state_for.assert_not_awaited()
    cases.set_state.assert_not_awaited()


async def test_the_log_names_a_draft_not_an_ai_output(decidable: tuple[Any, ...]) -> None:
    """object_type follows the collection. `ai_output` was the pre-rename name."""
    service, _, _, _, logs, _ = decidable

    await service.approve("org-1", "draft-1", "owner")

    assert logs.record.await_args.kwargs["object_type"] == "draft"


# --- edit -------------------------------------------------------------------


async def test_edit_supersedes_then_inserts_new_version(decidable: tuple[Any, ...]) -> None:
    service, collection, *_ = decidable

    new_row = await service.edit("org-1", "draft-1", "owner", "human text")

    # Order matters: insert-first would transiently double-occupy the slot.
    supersede_update = collection.update_one.await_args.args[1]
    assert supersede_update["$set"]["status"] == DraftStatus.superseded.value
    assert supersede_update["$unset"] == {"slot_active": ""}

    inserted = collection.insert_one.await_args.args[0]
    assert inserted["version"] == 2
    assert inserted["chain_id"] == "draft-1"
    assert inserted["parent_draft_id"] == "draft-1"
    assert inserted["source"] == "human"
    assert inserted["edited"] is True
    assert inserted["content"] == "human text"
    assert inserted["slot_active"] is True
    assert inserted["status"] == DraftStatus.pending.value
    assert new_row["version"] == 2


async def test_edit_of_a_decided_draft_returns_409(decidable: tuple[Any, ...]) -> None:
    service, collection, *_ = decidable
    collection.find_one.return_value = draft_row(status=DraftStatus.approved.value)

    with pytest.raises(HTTPException) as exc:
        await service.edit("org-1", "draft-1", "owner", "too late")

    assert exc.value.status_code == 409


async def test_edit_losing_the_supersede_race_returns_409(decidable: tuple[Any, ...]) -> None:
    service, collection, *_ = decidable
    collection.update_one.return_value = MagicMock(matched_count=0)

    with pytest.raises(HTTPException) as exc:
        await service.edit("org-1", "draft-1", "owner", "human text")

    assert exc.value.status_code == 409
    collection.insert_one.assert_not_awaited()


async def test_edit_refuses_when_the_case_closed_mid_edit(decidable: tuple[Any, ...]) -> None:
    """The slot is briefly empty between supersede and insert; a closure can land
    in that window. Re-reading narrows it — without transactions, nothing closes it."""
    service, collection, cases, *_ = decidable
    cases.assert_case_access.side_effect = [
        {"_id": "proj-1", "owner_id": "owner", "title": "Contract"},
        {"_id": "proj-1", "owner_id": "owner", "closure": {"approved_at": "now"}},
    ]

    with pytest.raises(HTTPException) as exc:
        await service.edit("org-1", "draft-1", "owner", "human text")

    assert exc.value.status_code == 409
    collection.insert_one.assert_not_awaited()


async def test_edit_refuses_when_the_task_closed_mid_edit(decidable: tuple[Any, ...]) -> None:
    """A Task draft re-reads the Task, not its parent Case. Tasks carry their own
    closure block, so the Case check would look at the wrong row."""
    service, collection, cases, *_ = decidable
    collection.find_one.return_value = draft_row(task_id="task-1")
    tasks = _task_holder(service)
    tasks.get_task.side_effect = [
        {"_id": "task-1", "created_by": "owner", "title": "Draft reply"},
        {"_id": "task-1", "created_by": "owner", "closure": {"approved_at": "now"}},
    ]

    with pytest.raises(HTTPException) as exc:
        await service.edit("org-1", "draft-1", "owner", "human text")

    assert exc.value.status_code == 409
    collection.insert_one.assert_not_awaited()
    cases.assert_case_access.assert_not_awaited()


async def test_edit_logs_character_counts_not_content(decidable: tuple[Any, ...]) -> None:
    service, _, _, _, logs, _ = decidable

    await service.edit("org-1", "draft-1", "owner", "much longer human text")

    payload = logs.record.await_args.kwargs["payload"]
    assert payload["from_version"] == 1
    assert payload["to_version"] == 2
    assert payload["chars_before"] == len("draft text")
    assert payload["chars_after"] == len("much longer human text")
    assert "content" not in payload
    assert logs.record.await_args.kwargs["object_type"] == "draft"


# --- deciding on a closed holder --------------------------------------------


async def test_approving_a_draft_on_a_closed_case_is_refused(
    decidable: tuple[Any, ...],
) -> None:
    """Approval is the one transition that moves the board, and a closed Case's
    column is frozen. Refused before the decision is written, not after — a draft
    marked approved with no board move is a state nothing can repair."""
    service, collection, cases, *_ = decidable
    cases.assert_case_access.return_value = {
        "_id": "proj-1",
        "owner_id": "owner",
        "closure": {"approved_at": "now"},
    }

    with pytest.raises(HTTPException) as exc:
        await service.approve("org-1", "draft-1", "owner")

    assert exc.value.status_code == 409
    collection.update_one.assert_not_awaited()
    cases.set_state.assert_not_awaited()


async def test_approving_a_draft_on_a_closed_task_is_refused(
    decidable: tuple[Any, ...],
) -> None:
    service, collection, *_ = decidable
    collection.find_one.return_value = draft_row(task_id="task-1")
    tasks = _task_holder(service)
    tasks.get_task.return_value = {
        "_id": "task-1",
        "created_by": "owner",
        "closure": {"approved_at": "now"},
    }

    with pytest.raises(HTTPException) as exc:
        await service.approve("org-1", "draft-1", "owner")

    assert exc.value.status_code == 409
    collection.update_one.assert_not_awaited()
    tasks.set_state.assert_not_awaited()


async def test_rejecting_a_draft_on_a_closed_case_is_allowed(
    decidable: tuple[Any, ...],
) -> None:
    """Reject moves no board, and it is the only way to clear a slot that a race
    left behind on a closed Case. Refusing it would strand the draft."""
    service, _, cases, *_ = decidable
    cases.assert_case_access.return_value = {
        "_id": "proj-1",
        "owner_id": "owner",
        "closure": {"approved_at": "now"},
    }

    row = await service.reject("org-1", "draft-1", "owner")

    assert row["status"] == DraftStatus.rejected.value


# --- closure guard ----------------------------------------------------------


async def test_case_guard_ignores_task_id_so_child_drafts_block_the_parent(
    service: DraftService, collection: MagicMock
) -> None:
    collection.find_one.return_value = draft_row(task_id="task-9")

    with pytest.raises(HTTPException) as exc:
        await service.assert_no_active_draft("org-1", "proj-1")

    assert exc.value.status_code == 409
    query = collection.find_one.await_args.args[0]
    assert "task_id" not in query
    assert query["slot_active"] is True


async def test_task_guard_scopes_to_its_own_task(
    service: DraftService, collection: MagicMock
) -> None:
    collection.find_one.return_value = None

    await service.assert_no_active_draft("org-1", "proj-1", task_id="task-1")

    query = collection.find_one.await_args.args[0]
    assert query["task_id"] == "task-1"


async def test_guard_passes_when_nothing_holds_the_slot(
    service: DraftService, collection: MagicMock
) -> None:
    collection.find_one.return_value = None

    await service.assert_no_active_draft("org-1", "proj-1")



# --- bounded generation -----------------------------------------------------


async def test_bounded_raises_when_the_wall_clock_passes() -> None:
    """An httpx read timeout resets on every byte, so a stream trickling one
    token every few seconds never trips it. The reclaim assumes a dead generation
    is really dead, which needs a wall clock too.

    Uses the real clock. Monkeypatching `draft_service.time.monotonic` — as an
    earlier draft did — patches the stdlib module itself, so asyncio's own event
    loop consumes the fake ticks and the test dies at teardown.
    """
    from services.draft_service import bounded

    async def trickle():
        yield "a"
        await asyncio.sleep(0.05)
        yield "b"

    collected = []
    with pytest.raises(TimeoutError):
        async for item in bounded(trickle(), 0.01):
            collected.append(item)

    # "a" arrived before the deadline; "b" arrived after and never yields.
    assert collected == ["a"]


async def test_bounded_passes_a_stream_that_finishes_in_time() -> None:
    from services.draft_service import bounded

    async def quick():
        yield "a"
        yield "b"

    assert [item async for item in bounded(quick(), 60)] == ["a", "b"]


# --- delegation -------------------------------------------------------------


@pytest.fixture
def delegatable(service: DraftService) -> tuple[Any, ...]:
    """A Case ready to delegate, with every collaborator stubbed."""
    order: list[str] = []

    async def reserve(**kwargs: Any) -> str:
        order.append("reserve")
        return "draft-1"

    async def charge(**kwargs: Any) -> None:
        order.append("charge")

    service.reserve_slot = AsyncMock(side_effect=reserve)  # type: ignore[method-assign]
    service.release_slot = AsyncMock()  # type: ignore[method-assign]

    cases = MagicMock()
    cases.assert_case_access = AsyncMock(
        return_value={
            "_id": "proj-1",
            "title": "Contract dispute",
            "objective": "Draft a reply",
            "case_type": "tax",
            "owner_id": "member-a",
        }
    )
    service._cases = lambda: cases  # type: ignore[method-assign]

    chat = MagicMock()
    # The real staticmethod, not a mock: a MagicMock attribute silently accepts
    # any query, so the length cap would be untested and could be deleted without
    # a single failure.
    chat.validate_query_length = ChatService.validate_query_length
    chat.verify_user_credits = AsyncMock(side_effect=charge)
    chat.extract_assistant_config = MagicMock(return_value=(1, {}))
    chat.create_streaming_response = MagicMock(return_value="STREAM")
    service._chat = lambda: chat  # type: ignore[method-assign]

    history = MagicMock()
    history.ensure_shared_session = AsyncMock(
        return_value={"_id": "ses-1", "session_id": "ses-1"}
    )
    history.create_message_id = MagicMock(return_value="msg-1")
    service._history = lambda: history  # type: ignore[method-assign]

    logs = MagicMock()
    logs.record = AsyncMock()
    service._logs = lambda: logs  # type: ignore[method-assign]

    return service, order, chat, logs


async def test_delegate_reserves_before_charging(delegatable: tuple[Any, ...]) -> None:
    """A losing racer must never be billed."""
    service, order, _, _ = delegatable

    result = await service.delegate(
        org_id="org-1",
        case_id="proj-1",
        task_id=None,
        user_id="member-a",
        instruction=None,
    )

    assert result == "STREAM"
    assert order == ["reserve", "charge"]


async def test_delegate_logs_the_request_with_instruction_flag(
    delegatable: tuple[Any, ...],
) -> None:
    service, _, _, logs = delegatable

    await service.delegate(
        org_id="org-1",
        case_id="proj-1",
        task_id=None,
        user_id="member-a",
        instruction="focus on VAT",
    )

    payload = logs.record.await_args.kwargs["payload"]
    assert payload["has_instruction"] is True
    assert payload["assistant"] == "tax"
    assert logs.record.await_args.kwargs["object_type"] == "draft"


async def test_delegate_never_takes_the_prompt_from_the_client(
    delegatable: tuple[Any, ...],
) -> None:
    """The instruction is one labelled part of a server-composed query, not the
    query itself — a client cannot replace the Case context with its own text."""
    service, _, _, _ = delegatable

    query = service._build_query(
        {"title": "Contract dispute", "objective": "Draft a reply"}, "focus on VAT"
    )

    assert query.startswith("Title: Contract dispute")
    assert "Objective: Draft a reply" in query
    assert "Instruction: focus on VAT" in query


async def test_delegating_on_a_closed_case_is_refused(
    delegatable: tuple[Any, ...],
) -> None:
    """Nothing new is generated for signed-off work. Refused before the slot is
    reserved, so a closed Case cannot even hold one."""
    service, order, _, _ = delegatable
    service._cases().assert_case_access.return_value = {
        "_id": "proj-1",
        "title": "Contract dispute",
        "case_type": "tax",
        "owner_id": "member-a",
        "closure": {"approved_at": "now"},
    }

    with pytest.raises(HTTPException) as exc:
        await service.delegate(
            org_id="org-1",
            case_id="proj-1",
            task_id=None,
            user_id="member-a",
            instruction=None,
        )

    assert exc.value.status_code == 409
    assert order == []


async def test_delegating_on_a_closed_task_is_refused(
    delegatable: tuple[Any, ...],
) -> None:
    service, order, _, _ = delegatable
    tasks = MagicMock()
    tasks.get_task = AsyncMock(
        return_value={
            "_id": "task-1",
            "case_id": "proj-1",
            "title": "Draft reply",
            "created_by": "member-a",
            "closure": {"approved_at": "now"},
        }
    )
    service._tasks = lambda: tasks  # type: ignore[method-assign]

    with pytest.raises(HTTPException) as exc:
        await service.delegate(
            org_id="org-1",
            case_id="proj-1",
            task_id="task-1",
            user_id="member-a",
            instruction=None,
        )

    assert exc.value.status_code == 409
    assert order == []


async def test_delegate_refuses_an_over_long_query_before_reserving(
    delegatable: tuple[Any, ...],
) -> None:
    """The same cap the chat endpoints apply, on the composed query rather than
    the raw instruction — the Case fields go to the model too. Checked before the
    slot, so an over-long request leaves nothing to reclaim."""
    service, order, _, _ = delegatable

    with pytest.raises(QueryTooLongException):
        await service.delegate(
            org_id="org-1",
            case_id="proj-1",
            task_id=None,
            user_id="member-a",
            instruction="x" * (settings.MAX_QUERY_LENGTH + 1),
        )

    assert order == []


async def test_delegate_refused_before_charging_when_slot_held(
    delegatable: tuple[Any, ...],
) -> None:
    """Built on the fixture, not from scratch: delegate() loads the Case before it
    reserves, so a test that stubs only reserve_slot reaches the real CaseService
    and fails with 404 long before the assertion it cares about."""
    service, _, chat, _ = delegatable
    service.reserve_slot = AsyncMock(  # type: ignore[method-assign]
        side_effect=HTTPException(status_code=409, detail="held")
    )

    with pytest.raises(HTTPException) as exc:
        await service.delegate(
            org_id="org-1",
            case_id="proj-1",
            task_id=None,
            user_id="member-a",
            instruction=None,
        )

    assert exc.value.status_code == 409
    chat.verify_user_credits.assert_not_awaited()


async def test_credit_refusal_releases_the_slot(delegatable: tuple[Any, ...]) -> None:
    """No credits must not leave the Case permanently un-delegatable."""
    service, _, chat, _ = delegatable
    chat.verify_user_credits = AsyncMock(
        side_effect=HTTPException(status_code=429, detail="no credits")
    )

    with pytest.raises(HTTPException):
        await service.delegate(
            org_id="org-1",
            case_id="proj-1",
            task_id=None,
            user_id="member-a",
            instruction=None,
        )

    service.release_slot.assert_awaited_once_with("draft-1")


# --- reads ------------------------------------------------------------------


def _cursor(rows: list[dict[str, Any]]) -> MagicMock:
    cursor = MagicMock()
    cursor.sort.return_value = cursor
    cursor.to_list = AsyncMock(return_value=rows)
    return cursor


async def test_chain_returns_versions_in_order(
    service: DraftService, collection: MagicMock
) -> None:
    """One query on chain_id, not a walk up parent_draft_id link by link."""
    collection.find.return_value = _cursor(
        [draft_row(version=1), draft_row(_id="draft-2", version=2)]
    )
    collection.find_one.return_value = draft_row()
    service._load_holder = AsyncMock(return_value={"title": "Contract"})  # type: ignore[method-assign]

    chain = await service.get_chain("org-1", "draft-1", "owner")

    assert [row["version"] for row in chain] == [1, 2]
    collection.find.return_value.sort.assert_called_once_with("version", 1)


async def test_chain_is_access_checked(
    service: DraftService, collection: MagicMock
) -> None:
    """A chain read must not be a way around the holder's permissions."""
    collection.find_one.return_value = draft_row()
    service._load_holder = AsyncMock(  # type: ignore[method-assign]
        side_effect=HTTPException(status_code=403, detail="no")
    )

    with pytest.raises(HTTPException) as exc:
        await service.get_chain("org-1", "draft-1", "stranger")

    assert exc.value.status_code == 403
    collection.find.assert_not_called()


async def test_list_endpoints_project_content_away(
    service: DraftService, collection: MagicMock
) -> None:
    """Draft bodies are not listing material — and this is a legal-text field."""
    collection.find.return_value = _cursor([])
    service._cases = lambda: MagicMock(  # type: ignore[method-assign]
        assert_case_access=AsyncMock(return_value={})
    )

    await service.list_for_case("org-1", "proj-1", "owner")

    assert collection.find.call_args.args[1] == {"content": 0}


async def test_list_for_task_scopes_to_the_task(
    service: DraftService, collection: MagicMock
) -> None:
    collection.find.return_value = _cursor([])
    service._tasks = lambda: MagicMock(get_task=AsyncMock(return_value={}))  # type: ignore[method-assign]

    await service.list_for_task("org-1", "task-1", "owner")

    assert collection.find.call_args.args[0] == {"org_id": "org-1", "task_id": "task-1"}


async def test_assert_draft_access_returns_the_row(
    service: DraftService, collection: MagicMock
) -> None:
    """Public, so the router never reaches into a private method to check access."""
    collection.find_one.return_value = draft_row()
    service._load_holder = AsyncMock(return_value={"title": "Contract"})  # type: ignore[method-assign]

    row = await service.assert_draft_access("org-1", "draft-1", "owner")

    assert row["_id"] == "draft-1"


def test_no_gate_route_admits_a_service_key() -> None:
    """The gate is JWT-only by construction. Mounting drafts.router the way the
    chat routers mount — with verify_user_or_service_auth — would let a machine
    approve its own draft, which is the one thing this feature exists to prevent."""
    from main import create_app
    from security.dependencies import verify_user_or_service_auth

    app = create_app()
    gate = [
        r
        for r in app.routes
        if "/drafts" in getattr(r, "path", "")
        or "delegate" in getattr(r, "path", "")
    ]

    # 9 originally, plus the two `delegate/prepare` routes and the draft export.
    # Prepare belongs behind the same JWT-only wall as the rest: it reads Case
    # text, and the export hands over a document the gate has passed.
    assert len(gate) == 12, f"expected 12 gate routes, found {len(gate)}"
    for route in gate:
        calls = [d.call for d in route.dependant.dependencies]  # type: ignore[attr-defined]
        assert verify_user_or_service_auth not in calls, getattr(route, "path", "")
