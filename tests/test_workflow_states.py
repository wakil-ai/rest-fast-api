"""Per-organization workflow boards: seeding, validation, and admin-only writes."""

import uuid
from datetime import datetime, timedelta, timezone
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import jwt
import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient
from pymongo.errors import DuplicateKeyError

from api.v2 import workflow_states as workflow_states_api
from core.config import settings
from models.workflow_states import StateAppliesTo, StateCategory
from security import dependencies as security_dependencies
from services.workflow_state_service import DEFAULT_STATES, WorkflowStateService


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
    app.include_router(workflow_states_api.router, prefix="/api/v2")
    return TestClient(app)


def state_doc(**overrides: Any) -> dict[str, Any]:
    doc: dict[str, Any] = {
        "_id": "wfst-1",
        "org_id": "org-1",
        "applies_to": "case",
        "name": "In Progress",
        "category": "in_progress",
        "order": 2,
        "is_system": False,
        "archived": False,
    }
    doc.update(overrides)
    return doc


class Svc:
    """A WorkflowStateService with its collaborators replaced.

    `orgs` is the shared organization guard; `coll` is the raw Motor collection the
    seeding upserts go through, which `find_documents` does not cover.
    """

    def __init__(self) -> None:
        self.svc = WorkflowStateService.__new__(WorkflowStateService)
        self.db = AsyncMock()
        self.coll = AsyncMock()
        self.db.mongo_handler.db = {"workflow_states": self.coll}
        self.svc.db = self.db  # type: ignore[misc]
        self.svc.collection = "workflow_states"
        self.svc.prefix = "wfst-"
        self.orgs = AsyncMock(unsafe=True)
        self.orgs.assert_org_member = AsyncMock(return_value={"_id": "org-1"})
        self.orgs.assert_org_admin = AsyncMock(return_value={"_id": "org-1"})


@pytest.fixture
def svc(monkeypatch: pytest.MonkeyPatch) -> Svc:
    built = Svc()
    monkeypatch.setattr(
        "services.workflow_state_service.get_organization_service",
        lambda: built.orgs,
    )
    return built


# --- seeding ----------------------------------------------------------------


async def test_seeding_creates_both_boards(svc: Svc) -> None:
    await svc.svc.ensure_default_states("org-1")

    calls = svc.coll.update_one.await_args_list
    assert len(calls) == len(DEFAULT_STATES) * 2  # one board per applies_to

    seeded = [c.args[1]["$setOnInsert"] for c in calls]
    assert {s["applies_to"] for s in seeded} == {"case", "task"}
    assert all(s["is_system"] for s in seeded)
    assert [s["name"] for s in seeded if s["applies_to"] == "case"] == [
        name for name, _ in DEFAULT_STATES
    ]
    assert [s["order"] for s in seeded if s["applies_to"] == "case"] == [1, 2, 3, 4, 5]
    # Every category is reachable, or closure has nowhere to move work to.
    assert {s["category"] for s in seeded} == {c.value for c in StateCategory}


async def test_seeding_is_an_upsert_not_an_insert(svc: Svc) -> None:
    """The unique index does not exist in production, so a plain insert would
    append another ten rows on every call with no duplicate error to catch."""
    await svc.svc.ensure_default_states("org-1")

    svc.db.insert_documents.assert_not_awaited()
    for call in svc.coll.update_one.await_args_list:
        assert call.kwargs["upsert"] is True
        # $setOnInsert, never $set: re-running must not overwrite a renamed state.
        assert set(call.args[1]) == {"$setOnInsert"}
        assert set(call.args[0]) == {"org_id", "applies_to", "name"}


async def test_list_seeds_a_board_that_comes_back_empty(svc: Svc) -> None:
    svc.db.find_documents = AsyncMock(side_effect=[[], [state_doc()]])

    result = await svc.svc.list_states("org-1", "user-a")

    assert svc.coll.update_one.await_count == len(DEFAULT_STATES) * 2
    assert [r["_id"] for r in result] == ["wfst-1"]


async def test_list_does_not_seed_a_board_that_already_exists(svc: Svc) -> None:
    """Seeding on every read would put twenty writes behind each board render."""
    svc.db.find_documents = AsyncMock(return_value=[state_doc()])

    await svc.svc.list_states("org-1", "user-a")

    svc.coll.update_one.assert_not_awaited()


async def test_list_requires_membership(svc: Svc) -> None:
    svc.orgs.assert_org_member = AsyncMock(
        side_effect=HTTPException(status_code=403, detail="Not a member")
    )

    with pytest.raises(HTTPException) as exc:
        await svc.svc.list_states("org-1", "outsider")

    assert exc.value.status_code == 403
    svc.coll.update_one.assert_not_awaited()


async def test_list_returns_the_board_in_order(svc: Svc) -> None:
    svc.db.find_documents = AsyncMock(
        return_value=[
            state_doc(_id="wfst-3", order=3),
            state_doc(_id="wfst-1", order=1),
            state_doc(_id="wfst-2", order=2),
        ]
    )

    result = await svc.svc.list_states("org-1", "user-a")

    assert [r["_id"] for r in result] == ["wfst-1", "wfst-2", "wfst-3"]


# --- assert_state_usable ----------------------------------------------------


async def test_state_usable_accepts_a_matching_state(svc: Svc) -> None:
    svc.db.find_documents = AsyncMock(return_value=[state_doc()])

    state = await svc.svc.assert_state_usable("org-1", "wfst-1", StateAppliesTo.case)

    assert state["_id"] == "wfst-1"


async def test_seeding_survives_losing_the_race_to_another_request(svc: Svc) -> None:
    """Seeding is lazy, so a board load and the first Case creation can collide.

    The upsert is not atomic against the unique index, so the loser sees E11000. The
    winner wrote the state we wanted, so the rest of the board must still be seeded.
    """
    svc.coll.update_one = AsyncMock(
        side_effect=[DuplicateKeyError("E11000")] + [None] * 99
    )

    await svc.svc.ensure_default_states("org-1")

    assert len(svc.coll.update_one.await_args_list) == len(DEFAULT_STATES) * 2


async def test_state_usable_rejects_another_organizations_state(svc: Svc) -> None:
    """The org_id is part of the lookup, so a foreign state reads as not found."""
    svc.db.find_documents = AsyncMock(return_value=[])

    with pytest.raises(HTTPException) as exc:
        await svc.svc.assert_state_usable("org-1", "wfst-other", StateAppliesTo.case)

    assert exc.value.status_code == 404
    assert svc.db.find_documents.await_args.args[1]["org_id"] == "org-1"


async def test_state_usable_rejects_the_wrong_board(svc: Svc) -> None:
    svc.db.find_documents = AsyncMock(return_value=[state_doc(applies_to="task")])

    with pytest.raises(HTTPException) as exc:
        await svc.svc.assert_state_usable("org-1", "wfst-1", StateAppliesTo.case)

    assert exc.value.status_code == 400


async def test_state_usable_rejects_an_archived_state(svc: Svc) -> None:
    svc.db.find_documents = AsyncMock(return_value=[])

    with pytest.raises(HTTPException):
        await svc.svc.assert_state_usable("org-1", "wfst-1", StateAppliesTo.case)

    assert svc.db.find_documents.await_args.args[1]["archived"] == {"$ne": True}


async def test_state_usable_rejects_a_closed_column(svc: Svc) -> None:
    """The closure bypass. `state_id` is an ordinary editable field, so without this
    an assignee could PATCH straight onto "Done": every dashboard and reminder keys
    on `category`, so the Case would read as closed while `status` stayed active and
    no admin ever signed it off."""
    svc.db.find_documents = AsyncMock(return_value=[state_doc(category="closed")])

    with pytest.raises(HTTPException) as exc:
        await svc.svc.assert_state_usable("org-1", "wfst-done", StateAppliesTo.case)

    assert exc.value.status_code == 400


async def test_the_approval_path_still_reaches_the_closed_column(svc: Svc) -> None:
    """default_state_for is how closure gets there, and it must stay open."""
    svc.db.find_documents = AsyncMock(
        return_value=[state_doc(_id="wfst-done", category="closed")]
    )

    state = await svc.svc.default_state_for(
        "org-1", StateAppliesTo.case, StateCategory.closed
    )

    assert state is not None and state["_id"] == "wfst-done"


async def test_default_state_for_picks_the_lowest_ordered_match(svc: Svc) -> None:
    svc.db.find_documents = AsyncMock(
        return_value=[
            state_doc(_id="wfst-late", category="closed", order=9),
            state_doc(_id="wfst-first", category="closed", order=5),
        ]
    )

    state = await svc.svc.default_state_for(
        "org-1", StateAppliesTo.case, StateCategory.closed
    )

    assert state is not None and state["_id"] == "wfst-first"


async def test_default_state_for_returns_none_when_the_category_is_gone(
    svc: Svc,
) -> None:
    svc.db.find_documents = AsyncMock(return_value=[])

    assert (
        await svc.svc.default_state_for(
            "org-1", StateAppliesTo.case, StateCategory.closed
        )
        is None
    )


# --- writes are admin-only --------------------------------------------------


async def test_create_requires_admin(svc: Svc) -> None:
    svc.orgs.assert_org_admin = AsyncMock(
        side_effect=HTTPException(status_code=403, detail="Admin only")
    )

    with pytest.raises(HTTPException) as exc:
        await svc.svc.create_state(
            "org-1",
            "member",
            {"name": "Sudda", "category": "in_progress", "applies_to": "case"},
        )

    assert exc.value.status_code == 403
    svc.db.insert_documents.assert_not_awaited()


async def test_create_appends_to_the_end_of_the_board(svc: Svc) -> None:
    svc.db.find_documents = AsyncMock(
        side_effect=[[], [state_doc(order=4), state_doc(order=7)]]
    )

    doc = await svc.svc.create_state(
        "org-1",
        "admin",
        {"name": "Sudda", "category": "in_progress", "applies_to": "case"},
    )

    assert doc["order"] == 8
    assert doc["is_system"] is False
    assert doc["_id"].startswith("wfst-")


async def test_create_rejects_a_duplicate_name(svc: Svc) -> None:
    svc.db.find_documents = AsyncMock(return_value=[state_doc()])

    with pytest.raises(HTTPException) as exc:
        await svc.svc.create_state(
            "org-1",
            "admin",
            {"name": "In Progress", "category": "in_progress", "applies_to": "case"},
        )

    assert exc.value.status_code == 409
    svc.db.insert_documents.assert_not_awaited()


async def test_update_requires_admin(svc: Svc) -> None:
    svc.orgs.assert_org_admin = AsyncMock(
        side_effect=HTTPException(status_code=403, detail="Admin only")
    )

    with pytest.raises(HTTPException) as exc:
        await svc.svc.update_state("org-1", "wfst-1", "member", {"name": "Renamed"})

    assert exc.value.status_code == 403
    svc.db.update_documents.assert_not_awaited()


async def test_a_default_state_can_be_renamed(svc: Svc) -> None:
    """Renaming is the whole point of states being rows; only deletion is refused."""
    svc.db.find_documents = AsyncMock(
        side_effect=[[state_doc(name="Done", category="closed", is_system=True)], []]
    )

    row = await svc.svc.update_state("org-1", "wfst-1", "admin", {"name": "Yakunlandi"})

    assert row["name"] == "Yakunlandi"
    assert row["category"] == "closed"  # meaning survives the rename
    assert svc.db.update_documents.await_args.args[2]["$set"]["name"] == "Yakunlandi"


async def test_update_never_writes_category_or_applies_to(svc: Svc) -> None:
    """Either would silently reclassify every Case already in the state."""
    svc.db.find_documents = AsyncMock(side_effect=[[state_doc()], []])

    await svc.svc.update_state(
        "org-1",
        "wfst-1",
        "admin",
        {"name": "Renamed", "category": "closed", "applies_to": "task", "order": 4},
    )

    assert set(svc.db.update_documents.await_args.args[2]["$set"]) == {
        "name",
        "order",
        "updated_at",
    }


async def test_update_with_an_unchanged_name_skips_the_clash_check(svc: Svc) -> None:
    svc.db.find_documents = AsyncMock(return_value=[state_doc(name="In Progress")])

    await svc.svc.update_state(
        "org-1", "wfst-1", "admin", {"name": "In Progress", "order": 9}
    )

    assert svc.db.update_documents.await_args.args[2]["$set"]["order"] == 9


async def test_archive_refuses_a_default_state(svc: Svc) -> None:
    svc.db.find_documents = AsyncMock(return_value=[state_doc(is_system=True)])

    with pytest.raises(HTTPException) as exc:
        await svc.svc.archive_state("org-1", "wfst-1", "admin")

    assert exc.value.status_code == 409
    svc.db.update_documents.assert_not_awaited()


async def test_archive_soft_deletes_a_custom_state(
    svc: Svc, monkeypatch: pytest.MonkeyPatch
) -> None:
    svc.db.find_documents = AsyncMock(return_value=[state_doc(is_system=False)])
    # Nothing is sitting in the column. The refuse-when-in-use path is covered in
    # tests/test_tasks.py, where Cases and Tasks exist to count.
    empty = AsyncMock(unsafe=True)
    empty.count_using_state = AsyncMock(return_value=0)
    import core.dependencies as deps

    monkeypatch.setattr(deps, "get_case_service", lambda: empty)
    monkeypatch.setattr(deps, "get_task_service", lambda: empty)

    row = await svc.svc.archive_state("org-1", "wfst-1", "admin")

    update = svc.db.update_documents.await_args.args[2]["$set"]
    assert update["archived"] is True and update["archived_at"] is not None
    assert row["archived"] is True


async def test_archive_requires_admin(svc: Svc) -> None:
    svc.orgs.assert_org_admin = AsyncMock(
        side_effect=HTTPException(status_code=403, detail="Admin only")
    )

    with pytest.raises(HTTPException) as exc:
        await svc.svc.archive_state("org-1", "wfst-1", "member")

    assert exc.value.status_code == 403
    svc.db.update_documents.assert_not_awaited()


# --- route wiring -----------------------------------------------------------


def test_every_route_rejects_a_request_without_a_token(client: TestClient) -> None:
    assert client.get("/api/v2/organizations/org-1/workflow-states").status_code == 401
    assert (
        client.post(
            "/api/v2/organizations/org-1/workflow-states",
            json={"name": "X", "category": "draft", "applies_to": "case"},
        ).status_code
        == 401
    )
    assert (
        client.patch(
            "/api/v2/organizations/org-1/workflow-states/wfst-1", json={"name": "X"}
        ).status_code
        == 401
    )
    assert (
        client.delete("/api/v2/organizations/org-1/workflow-states/wfst-1").status_code
        == 401
    )


def test_list_route_passes_the_token_subject_and_filter(
    jwt_settings: None, monkeypatch: pytest.MonkeyPatch, client: TestClient
) -> None:
    lister = AsyncMock(return_value=[state_doc()])
    monkeypatch.setattr(workflow_states_api.state_service, "list_states", lister)

    response = client.get(
        "/api/v2/organizations/org-1/workflow-states?applies_to=task",
        headers=auth("user-a"),
    )

    assert response.status_code == 200
    assert response.json()["states"][0]["state_id"] == "wfst-1"
    lister.assert_awaited_once_with("org-1", "user-a", applies_to=StateAppliesTo.task)


def test_create_route_passes_the_token_subject(
    jwt_settings: None, monkeypatch: pytest.MonkeyPatch, client: TestClient
) -> None:
    creator = AsyncMock(return_value=state_doc(name="Sudda"))
    monkeypatch.setattr(workflow_states_api.state_service, "create_state", creator)

    response = client.post(
        "/api/v2/organizations/org-1/workflow-states",
        json={"name": "Sudda", "category": "in_progress", "applies_to": "case"},
        headers=auth("user-a"),
    )

    assert response.status_code == 201
    creator.assert_awaited_once_with(
        "org-1",
        "user-a",
        {"name": "Sudda", "category": "in_progress", "applies_to": "case"},
    )


def test_patch_route_forwards_only_the_supplied_keys(
    jwt_settings: None, monkeypatch: pytest.MonkeyPatch, client: TestClient
) -> None:
    updater = AsyncMock(return_value=state_doc(name="Renamed"))
    monkeypatch.setattr(workflow_states_api.state_service, "update_state", updater)

    response = client.patch(
        "/api/v2/organizations/org-1/workflow-states/wfst-1",
        json={"name": "Renamed"},
        headers=auth("user-a"),
    )

    assert response.status_code == 200
    updater.assert_awaited_once_with("org-1", "wfst-1", "user-a", {"name": "Renamed"})


def test_delete_route_archives_as_the_token_subject(
    jwt_settings: None, monkeypatch: pytest.MonkeyPatch, client: TestClient
) -> None:
    archiver = AsyncMock(return_value=state_doc(archived=True))
    monkeypatch.setattr(workflow_states_api.state_service, "archive_state", archiver)

    response = client.delete(
        "/api/v2/organizations/org-1/workflow-states/wfst-1", headers=auth("user-a")
    )

    assert response.status_code == 204
    archiver.assert_awaited_once_with("org-1", "wfst-1", "user-a")


def test_create_route_rejects_an_unknown_category(
    jwt_settings: None, client: TestClient
) -> None:
    response = client.post(
        "/api/v2/organizations/org-1/workflow-states",
        json={"name": "X", "category": "not_a_category", "applies_to": "case"},
        headers=auth("user-a"),
    )

    assert response.status_code == 422
