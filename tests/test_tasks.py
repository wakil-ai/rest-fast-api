"""Tasks: parent scoping, permissions, closure, and the workflow-state in-use guard."""

import uuid
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock, call

import jwt
import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient

from api.v2 import tasks as tasks_api
from core.config import settings
from models.workflow_states import StateAppliesTo, StateCategory
from security import dependencies as security_dependencies
from services.task_service import TaskService
from services.workflow_state_service import WorkflowStateService


@pytest.fixture
def jwt_settings(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "JWT_SECRET_KEY", "test-jwt-secret-at-least-32-bytes")
    monkeypatch.setattr(settings, "JWT_ALGORITHM", "HS256")
    monkeypatch.setattr(settings, "JWT_ISSUER", "test-issuer")
    monkeypatch.setattr(settings, "JWT_AUDIENCE", "test-audience")


@pytest.fixture(autouse=True)
def auth_status(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        security_dependencies.redis_service, "cache_get", MagicMock(return_value=None)
    )
    monkeypatch.setattr(
        security_dependencies.redis_service, "cache_set", MagicMock(return_value=True)
    )
    monkeypatch.setattr(
        security_dependencies.chat_history_service,
        "get_user_auth_status",
        AsyncMock(
            return_value={"exists": True, "is_blocked": False, "archived": False}
        ),
    )


def frontend_token(user_id: str) -> str:
    secret = settings.JWT_SECRET_KEY
    assert secret is not None, "jwt_settings fixture must run before minting a token"
    now = datetime.now(timezone.utc)
    return jwt.encode(
        {
            "sub": user_id,
            "iat": now,
            "exp": now + timedelta(minutes=5),
            "jti": uuid.uuid4().hex,
            "typ": "access_token",
            "iss": settings.JWT_ISSUER,
            "aud": settings.JWT_AUDIENCE,
        },
        secret,
        algorithm=settings.JWT_ALGORITHM,
    )


def auth(user_id: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {frontend_token(user_id)}"}


@pytest.fixture
def client() -> TestClient:
    app = FastAPI()
    app.include_router(tasks_api.router, prefix="/api/v2")
    return TestClient(app)


def task_doc(**overrides: Any) -> dict[str, Any]:
    now = datetime.now(timezone.utc)
    doc: dict[str, Any] = {
        "_id": "task-1",
        "case_id": "proj-1",
        "org_id": "org-1",
        "title": "Draft the appeal",
        "description": None,
        "objective": None,
        "task_type": "court",
        "state_id": "wfst-draft",
        "assignee_id": "assignee",
        "closure": {},
        "created_by": "creator",
        "updated_by": "creator",
        "archived": False,
        "created_at": now,
        "updated_at": now,
    }
    doc.update(overrides)
    return doc


class Svc:
    def __init__(self) -> None:
        self.svc = TaskService.__new__(TaskService)
        self.db = AsyncMock()
        self.db.find_documents = AsyncMock(return_value=[task_doc()])
        # count_documents and update_many go straight at the driver collection
        # rather than through find_documents, so they need a handle of their own.
        self.coll = AsyncMock()
        self.coll.count_documents = AsyncMock(return_value=0)
        self.coll.update_one = AsyncMock(return_value=SimpleNamespace(modified_count=0))
        self.db.mongo_handler.db = {"tasks": self.coll}
        self.svc.db = self.db  # type: ignore[misc]
        self.svc.collection = "tasks"
        self.svc.prefix = "task-"

        self.orgs = AsyncMock(unsafe=True)
        self.orgs.assert_org_member = AsyncMock(return_value={"_id": "org-1"})
        self.orgs.assert_org_admin = AsyncMock(return_value={"_id": "org-1"})
        self.orgs.get_membership = AsyncMock(return_value={"role": "member"})

        self.states = AsyncMock(unsafe=True)
        self.states.assert_state_usable = AsyncMock(return_value={"_id": "wfst-x"})
        self.states.default_state_for = AsyncMock(return_value={"_id": "wfst-draft"})

        self.cases = AsyncMock(unsafe=True)
        self.cases.assert_case_access = AsyncMock(
            return_value={"_id": "proj-1", "org_id": "org-1"}
        )
        # create_task re-reads this after its insert; default to the Case surviving.
        self.cases.is_active = AsyncMock(return_value=True)

        # Stubbed, or the real service reaches a live Mongo from inside the tests.
        self.logs = AsyncMock(unsafe=True)

        self.svc._logs = lambda: self.logs  # type: ignore[method-assign]
        self.svc._orgs = lambda: self.orgs  # type: ignore[method-assign]
        self.svc._states = lambda: self.states  # type: ignore[method-assign]
        self.svc._cases = lambda: self.cases  # type: ignore[method-assign]
        # The closure guard's collaborator: nothing holds a draft slot by default.
        self.drafts = AsyncMock(unsafe=True)
        self.drafts.assert_no_active_draft = AsyncMock(return_value=None)
        self.svc._drafts = lambda: self.drafts  # type: ignore[method-assign]

    def sent(self) -> dict[str, Any]:
        return self.db.update_documents.await_args.args[2]["$set"]

    def inserted(self) -> dict[str, Any]:
        return self.db.insert_documents.await_args.args[1][0]

    def logged(self) -> list[str]:
        return [c.kwargs["event_type"].value for c in self.logs.record.await_args_list]


@pytest.fixture
def svc() -> Svc:
    return Svc()


# --- create -----------------------------------------------------------------


async def test_create_takes_org_and_case_from_the_parent(svc: Svc) -> None:
    """org_id comes from the Case, never the request, so the two cannot disagree."""
    doc = await svc.svc.create_task(
        "org-1", "proj-1", "member", {"title": "  Draft  ", "org_id": "org-evil"}
    )

    inserted = svc.inserted()
    assert inserted["org_id"] == "org-1"
    assert inserted["case_id"] == "proj-1"
    assert inserted["title"] == "Draft"
    assert inserted["created_by"] == "member"
    assert inserted["state_id"] == "wfst-draft"
    assert inserted["_id"].startswith("task-")
    assert doc["closure"] == {}
    assert svc.logged() == ["task.created"]
    svc.states.default_state_for.assert_awaited_once_with(
        "org-1", StateAppliesTo.task, StateCategory.draft
    )


async def test_create_refuses_a_case_in_another_organization(svc: Svc) -> None:
    """The parent is loaded through CaseService, which scopes by org and archived."""
    svc.cases.assert_case_access = AsyncMock(
        side_effect=HTTPException(status_code=404, detail="Case not found")
    )

    with pytest.raises(HTTPException) as exc:
        await svc.svc.create_task("org-1", "proj-other", "member", {"title": "X"})

    assert exc.value.status_code == 404
    svc.db.insert_documents.assert_not_awaited()


async def test_create_uses_the_task_board_not_the_case_board(svc: Svc) -> None:
    await svc.svc.create_task(
        "org-1", "proj-1", "member", {"title": "X", "state_id": "wfst-given"}
    )

    svc.states.assert_state_usable.assert_awaited_once_with(
        "org-1", "wfst-given", StateAppliesTo.task
    )


async def test_create_rejects_an_assignee_outside_the_organization(svc: Svc) -> None:
    svc.orgs.get_membership = AsyncMock(return_value=None)

    with pytest.raises(HTTPException) as exc:
        await svc.svc.create_task(
            "org-1", "proj-1", "member", {"title": "X", "assignee_id": "stranger"}
        )

    assert exc.value.status_code == 400
    svc.db.insert_documents.assert_not_awaited()


async def test_create_rejects_a_blank_title(svc: Svc) -> None:
    with pytest.raises(HTTPException) as exc:
        await svc.svc.create_task("org-1", "proj-1", "member", {"title": "  "})

    assert exc.value.status_code == 400


async def test_a_task_racing_the_archive_cascade_is_undone(svc: Svc) -> None:
    """The access check and the insert are not one atomic step. `archive_case` flips
    the Case and then drains its Tasks; an insert that passes the check before the
    flip and lands after the drain leaves a live Task on an archived Case — showing
    in the org-wide "my work" list pointing at a 404, and blocking archive_state
    with a count nobody can act on.

    Deleted, not archived: no event has been logged yet, so as far as the record is
    concerned the Task never existed.
    """
    svc.cases.is_active = AsyncMock(return_value=False)

    with pytest.raises(HTTPException) as exc:
        await svc.svc.create_task("org-1", "proj-1", "member", {"title": "X"})

    assert exc.value.status_code == 404
    task_id = svc.inserted()["_id"]
    svc.coll.delete_one.assert_awaited_once_with({"_id": task_id})
    assert svc.logged() == []


async def test_a_task_created_into_a_live_case_is_kept(svc: Svc) -> None:
    await svc.svc.create_task("org-1", "proj-1", "member", {"title": "X"})

    svc.coll.delete_one.assert_not_awaited()
    assert svc.logged() == ["task.created"]


# --- read and edit ----------------------------------------------------------


async def test_list_is_scoped_to_the_organization(svc: Svc) -> None:
    await svc.svc.list_tasks("org-1", "member", case_id="proj-1", assignee_id="a")

    query = svc.db.find_documents.await_args.args[1]
    assert query["org_id"] == "org-1"
    assert query["case_id"] == "proj-1"
    assert query["assignee_id"] == "a"
    assert query["archived"] == {"$ne": True}


async def test_a_task_from_another_organization_is_not_found(svc: Svc) -> None:
    svc.db.find_documents = AsyncMock(return_value=[])

    with pytest.raises(HTTPException) as exc:
        await svc.svc.get_task("org-1", "task-other", "member")

    assert exc.value.status_code == 404
    assert svc.db.find_documents.await_args.args[1]["org_id"] == "org-1"


@pytest.mark.parametrize("actor", ["creator", "assignee"])
async def test_creator_and_assignee_can_edit(svc: Svc, actor: str) -> None:
    await svc.svc.update_task("org-1", "task-1", actor, {"title": "Renamed"})

    assert svc.sent()["title"] == "Renamed"
    assert svc.sent()["updated_by"] == actor


async def test_an_admin_can_edit_any_task(svc: Svc) -> None:
    svc.orgs.get_membership = AsyncMock(return_value={"role": "admin"})

    await svc.svc.update_task("org-1", "task-1", "the-head", {"title": "Renamed"})

    assert svc.sent()["title"] == "Renamed"


async def test_an_unrelated_member_cannot_edit(svc: Svc) -> None:
    with pytest.raises(HTTPException) as exc:
        await svc.svc.update_task("org-1", "task-1", "bystander", {"title": "Nope"})

    assert exc.value.status_code == 403
    svc.db.update_documents.assert_not_awaited()
    # A rejected action leaves no trace in the timeline.
    assert svc.logged() == []


async def test_update_cannot_rewrite_parent_owner_or_closure(svc: Svc) -> None:
    await svc.svc.update_task(
        "org-1",
        "task-1",
        "creator",
        {
            "title": "Renamed",
            "case_id": "proj-other",
            "org_id": "org-evil",
            "created_by": "me",
            "closure": {"approved_by": "me"},
            "archived": True,
        },
    )

    assert set(svc.sent()) == {"title", "updated_by", "updated_at"}


async def test_archive_is_admin_only(svc: Svc) -> None:
    svc.orgs.assert_org_admin = AsyncMock(
        side_effect=HTTPException(status_code=403, detail="Admin only")
    )

    with pytest.raises(HTTPException) as exc:
        await svc.svc.archive_task("org-1", "task-1", "member")

    assert exc.value.status_code == 403
    svc.db.update_documents.assert_not_awaited()


# --- closure ----------------------------------------------------------------


async def test_request_closure_records_the_requester(svc: Svc) -> None:
    await svc.svc.request_closure("org-1", "task-1", "assignee")

    closure = svc.sent()["closure"]
    assert svc.logged() == ["closure.requested"]
    assert closure["requested_by"] == "assignee" and closure["requested_at"]
    assert "state_id" not in svc.sent()


async def test_approve_without_a_request_is_refused(svc: Svc) -> None:
    with pytest.raises(HTTPException) as exc:
        await svc.svc.approve_closure("org-1", "task-1", "the-head")

    assert exc.value.status_code == 409
    svc.db.update_documents.assert_not_awaited()


async def test_approve_closure_is_admin_only(svc: Svc) -> None:
    svc.orgs.assert_org_admin = AsyncMock(
        side_effect=HTTPException(status_code=403, detail="Admin only")
    )

    with pytest.raises(HTTPException) as exc:
        await svc.svc.approve_closure("org-1", "task-1", "assignee")

    assert exc.value.status_code == 403


async def test_approval_moves_closure_and_the_task_board_together(svc: Svc) -> None:
    svc.db.find_documents = AsyncMock(
        return_value=[task_doc(closure={"requested_by": "a", "requested_at": "t"})]
    )
    svc.states.default_state_for = AsyncMock(return_value={"_id": "wfst-task-done"})

    await svc.svc.approve_closure("org-1", "task-1", "the-head")

    sent = svc.sent()
    assert sent["state_id"] == "wfst-task-done"
    assert sent["closure"]["approved_by"] == "the-head"
    svc.states.default_state_for.assert_awaited_once_with(
        "org-1", StateAppliesTo.task, StateCategory.closed
    )
    assert svc.logged() == ["closure.approved"]


async def test_approving_twice_is_refused(svc: Svc) -> None:
    svc.db.find_documents = AsyncMock(
        return_value=[task_doc(closure={"requested_at": "t", "approved_at": "t2"})]
    )

    with pytest.raises(HTTPException) as exc:
        await svc.svc.approve_closure("org-1", "task-1", "the-head")

    assert exc.value.status_code == 409


async def test_a_closed_task_cannot_be_moved_off_the_closed_column(svc: Svc) -> None:
    svc.db.find_documents = AsyncMock(
        return_value=[
            task_doc(
                state_id="wfst-done",
                closure={"approved_by": "head", "approved_at": "t"},
            )
        ]
    )

    with pytest.raises(HTTPException) as exc:
        await svc.svc.update_task(
            "org-1", "task-1", "assignee", {"state_id": "wfst-progress"}
        )

    assert exc.value.status_code == 409


async def test_set_state_will_not_move_a_closed_task_either(svc: Svc) -> None:
    """Same freeze as `update_task`, on the door other services use. DraftService's
    board move goes through here and would otherwise skip the rule entirely."""
    svc.db.find_documents = AsyncMock(
        return_value=[
            task_doc(
                state_id="wfst-done",
                closure={"approved_by": "head", "approved_at": "t"},
            )
        ]
    )

    with pytest.raises(HTTPException) as exc:
        await svc.svc.set_state("org-1", "task-1", "wfst-review", "assignee")

    assert exc.value.status_code == 409
    svc.db.update_documents.assert_not_awaited()


async def test_set_state_moves_an_open_task(svc: Svc) -> None:
    svc.db.find_documents = AsyncMock(return_value=[task_doc(state_id="wfst-todo")])

    row = await svc.svc.set_state("org-1", "task-1", "wfst-review", "assignee")

    assert row["state_id"] == "wfst-review"


async def test_reopening_a_task_clears_the_closure(svc: Svc) -> None:
    svc.db.find_documents = AsyncMock(
        return_value=[
            task_doc(
                state_id="wfst-done",
                closure={"approved_by": "head", "approved_at": "t"},
            )
        ]
    )
    svc.states.default_state_for = AsyncMock(return_value={"_id": "wfst-draft"})

    await svc.svc.reopen("org-1", "task-1", "the-head")

    sent = svc.sent()
    assert sent["closure"] == {}
    assert sent["state_id"] == "wfst-draft"
    assert [c.kwargs["event_type"].value for c in svc.logs.record.await_args_list] == [
        "closure.reopened"
    ]


async def test_reopening_a_task_is_admin_only(svc: Svc) -> None:
    svc.orgs.assert_org_admin = AsyncMock(
        side_effect=HTTPException(status_code=403, detail="Admin only")
    )

    with pytest.raises(HTTPException) as exc:
        await svc.svc.reopen("org-1", "task-1", "member")

    assert exc.value.status_code == 403


async def test_clearing_the_state_is_refused(svc: Svc) -> None:
    with pytest.raises(HTTPException) as exc:
        await svc.svc.update_task("org-1", "task-1", "assignee", {"state_id": None})

    assert exc.value.status_code == 400
    svc.db.update_documents.assert_not_awaited()


async def test_count_using_state_is_org_scoped_and_skips_archived(svc: Svc) -> None:
    svc.coll.count_documents = AsyncMock(return_value=2)

    assert await svc.svc.count_using_state("org-1", "wfst-1") == 2

    query = svc.coll.count_documents.await_args.args[0]
    assert query == {"org_id": "org-1", "state_id": "wfst-1", "archived": {"$ne": True}}


async def test_count_using_state_is_not_capped_by_a_fetch_limit(svc: Svc) -> None:
    """The number is shown to the admin, so it must be the real one."""
    svc.coll.count_documents = AsyncMock(return_value=873)

    assert await svc.svc.count_using_state("org-1", "wfst-1") == 873
    svc.db.find_documents.assert_not_awaited()


async def test_archive_for_case_takes_only_that_cases_live_tasks(svc: Svc) -> None:
    svc.db.find_documents = AsyncMock(
        side_effect=[[task_doc(), task_doc(_id="task-2")], []]
    )
    svc.coll.update_one = AsyncMock(return_value=SimpleNamespace(modified_count=1))

    assert await svc.svc.archive_for_case("org-1", "proj-1", "the-head") == 2

    assert svc.db.find_documents.await_args_list[0].args[1] == {
        "org_id": "org-1",
        "case_id": "proj-1",
        "archived": {"$ne": True},
    }
    query, update = svc.coll.update_one.await_args.args
    assert query == {"_id": "task-2", "archived": {"$ne": True}}
    assert update["$set"]["archived"] is True
    assert update["$set"]["updated_by"] == "the-head"


async def test_a_cascaded_archive_still_reaches_each_tasks_timeline(svc: Svc) -> None:
    """An evidence log must not go silent exactly when the Task was archived."""
    svc.db.find_documents = AsyncMock(
        side_effect=[[task_doc(), task_doc(_id="task-2")], []]
    )
    svc.coll.update_one = AsyncMock(return_value=SimpleNamespace(modified_count=1))

    await svc.svc.archive_for_case("org-1", "proj-1", "the-head")

    logged = svc.logs.record.await_args_list
    assert [c.kwargs["event_type"].value for c in logged] == [
        "task.archived",
        "task.archived",
    ]
    assert {c.kwargs["task_id"] for c in logged} == {"task-1", "task-2"}
    assert all(c.kwargs["payload"] == {"cascade": "case.archived"} for c in logged)


async def test_a_task_archived_by_somebody_else_is_not_claimed(svc: Svc) -> None:
    """The read is a stale snapshot. Logging every row in it would blame the cascade
    for an archive another admin performed in the window."""
    svc.db.find_documents = AsyncMock(
        side_effect=[[task_doc(), task_doc(_id="task-2")], []]
    )
    svc.coll.update_one = AsyncMock(
        side_effect=[
            SimpleNamespace(modified_count=0),  # already archived by another admin
            SimpleNamespace(modified_count=1),
        ]
    )

    assert await svc.svc.archive_for_case("org-1", "proj-1", "the-head") == 1

    logged = svc.logs.record.await_args_list
    assert [c.kwargs["task_id"] for c in logged] == ["task-2"]


async def test_the_cascade_keeps_going_past_one_batch(svc: Svc) -> None:
    """A Case with more Tasks than one page must not leave the tail live."""
    svc.db.find_documents = AsyncMock(
        side_effect=[[task_doc()], [task_doc(_id="task-2")], []]
    )
    svc.coll.update_one = AsyncMock(return_value=SimpleNamespace(modified_count=1))

    assert await svc.svc.archive_for_case("org-1", "proj-1", "the-head") == 2
    assert svc.db.find_documents.await_count == 3


async def test_a_case_with_no_live_tasks_writes_nothing(svc: Svc) -> None:
    svc.db.find_documents = AsyncMock(return_value=[])

    assert await svc.svc.archive_for_case("org-1", "proj-1", "the-head") == 0

    svc.coll.update_one.assert_not_awaited()
    svc.logs.record.assert_not_awaited()


async def test_assert_task_in_case_rejects_another_cases_task(svc: Svc) -> None:
    svc.db.find_documents = AsyncMock(return_value=[task_doc(case_id="proj-other")])

    with pytest.raises(HTTPException) as exc:
        await svc.svc.assert_task_in_case("org-1", "proj-1", "task-1")

    assert exc.value.status_code == 400
    # The caller already proved membership; re-checking it is a wasted round trip.
    svc.orgs.assert_org_member.assert_not_awaited()


async def test_a_closed_task_stays_editable(svc: Svc) -> None:
    svc.db.find_documents = AsyncMock(return_value=[task_doc(state_id="wfst-done")])
    svc.states.assert_state_usable = AsyncMock(
        side_effect=HTTPException(status_code=400, detail="closed")
    )

    row = await svc.svc.update_task(
        "org-1", "task-1", "assignee", {"title": "Fixed typo", "state_id": "wfst-done"}
    )

    assert row["title"] == "Fixed typo"
    svc.states.assert_state_usable.assert_not_awaited()


# --- the in-use guard deferred out of Phase 1 -------------------------------


def build_state_service(monkeypatch: pytest.MonkeyPatch, in_use: int, applies_to: str):
    svc = WorkflowStateService.__new__(WorkflowStateService)
    db = AsyncMock()
    db.find_documents = AsyncMock(
        return_value=[
            {
                "_id": "wfst-1",
                "org_id": "org-1",
                "applies_to": applies_to,
                "name": "Sudda",
                "category": "in_progress",
                "order": 3,
                "is_system": False,
            }
        ]
    )
    svc.db = db  # type: ignore[misc]
    svc.collection = "workflow_states"
    svc.prefix = "wfst-"

    orgs = AsyncMock(unsafe=True)
    orgs.assert_org_admin = AsyncMock(return_value={"_id": "org-1"})
    monkeypatch.setattr(
        "services.workflow_state_service.get_organization_service", lambda: orgs
    )

    # Two DISTINCT counters. A single shared mock would make routing to the wrong
    # collection invisible — the Case board must be counted against Cases and the
    # Task board against Tasks.
    cases = AsyncMock(unsafe=True)
    cases.count_using_state = AsyncMock(
        return_value=in_use if applies_to == "case" else 0
    )
    tasks = AsyncMock(unsafe=True)
    tasks.count_using_state = AsyncMock(
        return_value=in_use if applies_to == "task" else 0
    )
    import core.dependencies as deps

    monkeypatch.setattr(deps, "get_case_service", lambda: cases)
    monkeypatch.setattr(deps, "get_task_service", lambda: tasks)
    return svc, db, (cases, tasks)


async def test_archiving_a_state_with_tasks_in_it_is_refused(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Refusing beats silently moving a customer's work to another column."""
    svc, db, _ = build_state_service(monkeypatch, in_use=4, applies_to="task")

    with pytest.raises(HTTPException) as exc:
        await svc.archive_state("org-1", "wfst-1", "the-head")

    assert exc.value.status_code == 409
    assert "4" in str(exc.value.detail) and "Task" in str(exc.value.detail)
    db.update_documents.assert_not_awaited()


async def test_archiving_a_state_with_cases_in_it_is_refused(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    svc, db, _ = build_state_service(monkeypatch, in_use=2, applies_to="case")

    with pytest.raises(HTTPException) as exc:
        await svc.archive_state("org-1", "wfst-1", "the-head")

    assert exc.value.status_code == 409
    assert "Case" in str(exc.value.detail)
    db.update_documents.assert_not_awaited()


async def test_an_empty_state_still_archives(monkeypatch: pytest.MonkeyPatch) -> None:
    svc, db, _ = build_state_service(monkeypatch, in_use=0, applies_to="case")

    row = await svc.archive_state("org-1", "wfst-1", "the-head")

    assert row["archived"] is True
    assert db.update_documents.await_args.args[2]["$set"]["archived"] is True


@pytest.mark.parametrize("applies_to", ["case", "task"])
async def test_the_guard_counts_against_the_right_collection(
    monkeypatch: pytest.MonkeyPatch, applies_to: str
) -> None:
    """A task-board column counts Tasks; a case-board column counts Cases.

    Counting the wrong collection would let an admin archive a column that live
    work is sitting in, pushing it onto an invisible board position.
    """
    svc, _, (cases, tasks) = build_state_service(
        monkeypatch, in_use=0, applies_to=applies_to
    )

    await svc.archive_state("org-1", "wfst-1", "the-head")

    used, unused = (cases, tasks) if applies_to == "case" else (tasks, cases)
    # Twice: once as the guard, once after the write to catch anything that moved
    # in while the column was still live.
    assert used.count_using_state.await_args_list == [
        call("org-1", "wfst-1"),
        call("org-1", "wfst-1"),
    ]
    unused.count_using_state.assert_not_awaited()


async def test_work_that_slips_in_mid_archive_undoes_the_archive(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The column is live until the write lands, so a Case can be PATCHed into it
    after the guard has already counted zero. That item would sit on an archived
    state — off every board and invisible to the count that is meant to protect it."""
    svc, db, (cases, _) = build_state_service(monkeypatch, in_use=0, applies_to="case")
    # Clean on the way in, occupied by the time the write has landed.
    cases.count_using_state = AsyncMock(side_effect=[0, 1])

    with pytest.raises(HTTPException) as exc:
        await svc.archive_state("org-1", "wfst-1", "the-head")

    assert exc.value.status_code == 409
    # The archive is rolled back, or the state stays deleted with work sitting on it.
    assert db.update_documents.await_args.args[2]["$set"]["archived"] is False


# --- sessions carry a task scope -------------------------------------------


async def test_a_session_can_be_tagged_with_a_task() -> None:
    """A scope tag only — access is already gated by assert_session_access."""
    from services.chat_history_service import ChatHistoryService

    svc = ChatHistoryService.__new__(ChatHistoryService)
    svc.db_manager = AsyncMock()  # type: ignore[misc]
    svc.sessions_collection = "sessions"
    svc._ensure_user_exists = AsyncMock()  # type: ignore[method-assign]

    session = await svc.create_session(
        user_id="user-a", project_id="proj-1", task_id="task-1"
    )

    assert session["task_id"] == "task-1"
    assert session["project_id"] == "proj-1"


async def test_a_session_without_a_task_stores_none() -> None:
    from services.chat_history_service import ChatHistoryService

    svc = ChatHistoryService.__new__(ChatHistoryService)
    svc.db_manager = AsyncMock()  # type: ignore[misc]
    svc.sessions_collection = "sessions"
    svc._ensure_user_exists = AsyncMock()  # type: ignore[method-assign]

    session = await svc.create_session(user_id="user-a")

    assert session["task_id"] is None


# --- route wiring -----------------------------------------------------------


def test_every_route_rejects_a_request_without_a_token(client: TestClient) -> None:
    org = "/api/v2/organizations/org-1"
    assert (
        client.post(f"{org}/cases/proj-1/tasks", json={"title": "X"}).status_code == 401
    )
    assert client.get(f"{org}/cases/proj-1/tasks").status_code == 401
    assert client.get(f"{org}/tasks").status_code == 401
    assert client.get(f"{org}/tasks/task-1").status_code == 401
    assert client.patch(f"{org}/tasks/task-1", json={"title": "X"}).status_code == 401
    assert client.delete(f"{org}/tasks/task-1").status_code == 401
    assert client.post(f"{org}/tasks/task-1/closure/request").status_code == 401
    assert client.post(f"{org}/tasks/task-1/closure/approve").status_code == 401


def test_create_route_uses_the_token_subject(
    jwt_settings: None, monkeypatch: pytest.MonkeyPatch, client: TestClient
) -> None:
    creator = AsyncMock(return_value=task_doc())
    monkeypatch.setattr(tasks_api.task_service, "create_task", creator)

    response = client.post(
        "/api/v2/organizations/org-1/cases/proj-1/tasks",
        json={"title": "Draft", "created_by": "somebody-else"},
        headers=auth("user-a"),
    )

    assert response.status_code == 201
    assert response.json()["task_id"] == "task-1"
    creator.assert_awaited_once_with("org-1", "proj-1", "user-a", {"title": "Draft"})


def test_the_my_work_view_filters_by_assignee(
    jwt_settings: None, monkeypatch: pytest.MonkeyPatch, client: TestClient
) -> None:
    lister = AsyncMock(return_value=[task_doc()])
    monkeypatch.setattr(tasks_api.task_service, "list_tasks", lister)

    response = client.get(
        "/api/v2/organizations/org-1/tasks?assignee_id=user-a", headers=auth("user-a")
    )

    assert response.status_code == 200
    assert response.json()["case_id"] is None
    lister.assert_awaited_once_with(
        "org-1", "user-a", assignee_id="user-a", state_id=None, limit=100, skip=0
    )


def test_the_case_scoped_list_reports_its_case(
    jwt_settings: None, monkeypatch: pytest.MonkeyPatch, client: TestClient
) -> None:
    lister = AsyncMock(return_value=[task_doc()])
    monkeypatch.setattr(tasks_api.task_service, "list_tasks", lister)

    response = client.get(
        "/api/v2/organizations/org-1/cases/proj-1/tasks", headers=auth("user-a")
    )

    assert response.json()["case_id"] == "proj-1"
    lister.assert_awaited_once_with(
        "org-1",
        "user-a",
        case_id="proj-1",
        assignee_id=None,
        state_id=None,
        limit=100,
        skip=0,
    )


def test_the_case_scoped_list_can_be_paged(
    jwt_settings: None, monkeypatch: pytest.MonkeyPatch, client: TestClient
) -> None:
    """Without skip, a Case with more Tasks than one page hides the rest for good."""
    lister = AsyncMock(return_value=[])
    monkeypatch.setattr(tasks_api.task_service, "list_tasks", lister)

    client.get(
        "/api/v2/organizations/org-1/cases/proj-1/tasks?limit=50&skip=50",
        headers=auth("user-a"),
    )

    assert lister.await_args.kwargs["limit"] == 50
    assert lister.await_args.kwargs["skip"] == 50

    base = "/api/v2/organizations/org-1/cases/proj-1/tasks"
    assert client.get(f"{base}?limit=99999", headers=auth("user-a")).status_code == 422


def test_closure_routes_use_the_token_subject(
    jwt_settings: None, monkeypatch: pytest.MonkeyPatch, client: TestClient
) -> None:
    req = AsyncMock(return_value=task_doc())
    apr = AsyncMock(return_value=task_doc())
    monkeypatch.setattr(tasks_api.task_service, "request_closure", req)
    monkeypatch.setattr(tasks_api.task_service, "approve_closure", apr)

    base = "/api/v2/organizations/org-1/tasks/task-1/closure"
    assert client.post(f"{base}/request", headers=auth("user-a")).status_code == 200
    assert client.post(f"{base}/approve", headers=auth("head")).status_code == 200

    req.assert_awaited_once_with("org-1", "task-1", "user-a")
    apr.assert_awaited_once_with("org-1", "task-1", "head")


def test_create_route_rejects_an_unknown_task_type(
    jwt_settings: None, client: TestClient
) -> None:
    response = client.post(
        "/api/v2/organizations/org-1/cases/proj-1/tasks",
        json={"title": "X", "task_type": "not_an_assistant"},
        headers=auth("user-a"),
    )

    assert response.status_code == 422
