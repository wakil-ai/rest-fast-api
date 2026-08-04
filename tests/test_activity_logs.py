"""Activity logs: append-only, snapshotted actors, and never fatal to the action."""

import uuid
from datetime import datetime, timedelta, timezone
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import jwt
import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient

from api.v2 import activity_logs as logs_api
from core.config import settings
from models.activity_logs import ActorType, EventType
from security import dependencies as security_dependencies
from services.activity_log_service import ActivityLogService


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
    app.include_router(logs_api.router, prefix="/api/v2")
    return TestClient(app)


def log_doc(**overrides: Any) -> dict[str, Any]:
    doc: dict[str, Any] = {
        "_id": "alog-1",
        "org_id": "org-1",
        "occurred_at": datetime.now(timezone.utc),
        "event_type": EventType.case_created.value,
        "actor": {"id": "user-a", "type": "user", "role": "admin", "name": "Akbar"},
        "on_behalf_of": None,
        "object": {"type": "case", "id": "proj-1", "label": "Tax dispute"},
        "case_id": "proj-1",
        "task_id": None,
        "payload": {},
    }
    doc.update(overrides)
    return doc


class Svc:
    def __init__(self, monkeypatch: pytest.MonkeyPatch) -> None:
        self.svc = ActivityLogService.__new__(ActivityLogService)
        self.db = AsyncMock()
        self.coll = MagicMock()
        self.db.mongo_handler.db = {"activity_logs": self.coll}
        self.svc.db = self.db  # type: ignore[misc]
        self.svc.collection = "activity_logs"
        self.svc.prefix = "alog-"

        self.orgs = AsyncMock(unsafe=True)
        self.orgs.assert_org_member = AsyncMock(return_value={"_id": "org-1"})
        self.orgs.get_membership = AsyncMock(return_value={"role": "admin"})
        self.history = AsyncMock()
        self.history.get_user = AsyncMock(
            return_value={"first_name": "Akbar", "last_name": "Ahmadjonov"}
        )
        monkeypatch.setattr(
            "services.activity_log_service.get_organization_service", lambda: self.orgs
        )
        monkeypatch.setattr(
            "services.activity_log_service.get_chat_history_service",
            lambda: self.history,
        )

    def written(self) -> dict[str, Any]:
        return self.db.insert_documents.await_args.args[1][0]


@pytest.fixture
def svc(monkeypatch: pytest.MonkeyPatch) -> Svc:
    return Svc(monkeypatch)


# --- append-only ------------------------------------------------------------


def test_the_service_exposes_no_way_to_change_history() -> None:
    """ZRU-1115 evidence. A correction is a new event, never an edit.

    Asserted by attribute so that adding an update or delete method fails here.
    """
    forbidden = [
        name
        for name in dir(ActivityLogService)
        if any(word in name.lower() for word in ("update", "delete", "archive", "edit"))
    ]
    assert forbidden == []


def test_enterprise_collections_stay_out_of_the_personal_archive_sweep() -> None:
    """Deleting a personal account must not erase the department's work.

    Tasks, boards and memberships belong to the organization; activity logs are
    legal evidence. `projects` is the one that appears, because it holds personal
    projects too — and it carries a scope predicate that excludes Cases.
    """
    from services.account_archive_service import AccountArchiveService

    svc = AccountArchiveService()
    swept = set(svc._owned_collections)  # type: ignore[reportPrivateUsage]

    for collection in (
        settings.TASKS_COLLECTION,
        settings.WORKFLOW_STATES_COLLECTION,
        settings.ACTIVITY_LOGS_COLLECTION,
        settings.ORGANIZATIONS_COLLECTION,
        settings.ORGANIZATION_MEMBERS_COLLECTION,
    ):
        assert collection not in swept, f"{collection} is org-owned, not user-owned"

    assert settings.PROJECTS_COLLECTION in swept


async def test_a_log_row_carries_no_archived_flag(svc: Svc) -> None:
    await svc.svc.record(
        org_id="org-1",
        event_type=EventType.case_created,
        actor_id="user-a",
        object_type="case",
        object_id="proj-1",
    )

    assert "archived" not in svc.written()


# --- writing ----------------------------------------------------------------


async def test_record_snapshots_the_actors_role_and_name(svc: Svc) -> None:
    """Roles change and members leave; the log must read correctly years later."""
    await svc.svc.record(
        org_id="org-1",
        event_type=EventType.case_created,
        actor_id="user-a",
        object_type="case",
        object_id="proj-1",
        object_label="Tax dispute",
        case_id="proj-1",
    )

    doc = svc.written()
    assert doc["actor"] == {
        "id": "user-a",
        "type": "user",
        "role": "admin",
        "name": "Akbar Ahmadjonov",
    }
    assert doc["object"] == {
        "type": "case",
        "id": "proj-1",
        "label": "Tax dispute",
    }
    assert doc["_id"].startswith("alog-")
    assert doc["event_type"] == "case.created"


async def test_record_falls_back_to_the_username(svc: Svc) -> None:
    svc.history.get_user = AsyncMock(return_value={"username": "akbar"})

    await svc.svc.record(
        org_id="org-1",
        event_type=EventType.case_created,
        actor_id="user-a",
        object_type="case",
        object_id="proj-1",
    )

    assert svc.written()["actor"]["name"] == "akbar"


async def test_a_system_actor_needs_no_membership_lookup(svc: Svc) -> None:
    await svc.svc.record(
        org_id="org-1",
        event_type=EventType.case_archived,
        actor_id="reminder-job",
        object_type="case",
        object_id="proj-1",
        actor_type=ActorType.system,
    )

    assert svc.written()["actor"]["type"] == "system"
    svc.orgs.get_membership.assert_not_awaited()


async def test_a_failed_write_never_breaks_the_action_being_logged(svc: Svc) -> None:
    """An audit trail that can take down Case creation is worse than a gap."""
    svc.db.insert_documents = AsyncMock(side_effect=RuntimeError("mongo is down"))

    result = await svc.svc.record(
        org_id="org-1",
        event_type=EventType.case_created,
        actor_id="user-a",
        object_type="case",
        object_id="proj-1",
    )

    assert result is None


async def test_a_failed_actor_lookup_is_also_swallowed(svc: Svc) -> None:
    svc.orgs.get_membership = AsyncMock(side_effect=RuntimeError("mongo is down"))

    assert (
        await svc.svc.record(
            org_id="org-1",
            event_type=EventType.case_created,
            actor_id="user-a",
            object_type="case",
            object_id="proj-1",
        )
        is None
    )


# --- record_changes ---------------------------------------------------------


def recorded_events(svc: Svc) -> list[tuple[str, dict[str, Any]]]:
    return [
        (c.args[1][0]["event_type"], c.args[1][0]["payload"])
        for c in svc.db.insert_documents.await_args_list
    ]


async def test_a_state_move_carries_both_from_and_to(svc: Svc) -> None:
    """A state change without the previous value is half a fact."""
    await svc.svc.record_changes(
        kind="case",
        previous={"_id": "proj-1", "org_id": "org-1", "state_id": "wfst-a"},
        set_fields={"state_id": "wfst-b"},
        actor_id="user-a",
        case_id="proj-1",
    )

    assert recorded_events(svc) == [
        ("case.state_changed", {"from": "wfst-a", "to": "wfst-b"})
    ]


async def test_a_reassignment_is_its_own_event(svc: Svc) -> None:
    await svc.svc.record_changes(
        kind="task",
        previous={"_id": "task-1", "org_id": "org-1", "assignee_id": "old"},
        set_fields={"assignee_id": "new"},
        actor_id="user-a",
        case_id="proj-1",
        task_id="task-1",
    )

    assert recorded_events(svc) == [("task.assigned", {"from": "old", "to": "new"})]


async def test_one_edit_can_produce_several_events(svc: Svc) -> None:
    await svc.svc.record_changes(
        kind="case",
        previous={"_id": "proj-1", "org_id": "org-1", "state_id": "a", "title": "Old"},
        set_fields={
            "state_id": "b",
            "assignee_id": "new",
            "title": "New",
            "deadline": "2026-09-01",
            "updated_by": "user-a",
            "updated_at": "now",
        },
        actor_id="user-a",
        case_id="proj-1",
    )

    events = recorded_events(svc)
    assert [e for e, _ in events] == [
        "case.state_changed",
        "case.assigned",
        "case.updated",
    ]
    # Audit bookkeeping is not a user-visible change.
    assert events[2][1] == {"fields": ["deadline", "title"]}


async def test_an_unchanged_value_produces_no_event(svc: Svc) -> None:
    await svc.svc.record_changes(
        kind="case",
        previous={"_id": "proj-1", "org_id": "org-1", "state_id": "same"},
        set_fields={"state_id": "same", "updated_by": "u", "updated_at": "now"},
        actor_id="user-a",
        case_id="proj-1",
    )

    svc.db.insert_documents.assert_not_awaited()


# --- reading ----------------------------------------------------------------


async def test_list_requires_membership(svc: Svc) -> None:
    svc.orgs.assert_org_member = AsyncMock(
        side_effect=HTTPException(status_code=403, detail="Not a member")
    )

    with pytest.raises(HTTPException) as exc:
        await svc.svc.list_logs("org-1", "outsider")

    assert exc.value.status_code == 403


async def test_list_sorts_newest_first_on_id_not_created_at(svc: Svc) -> None:
    """db.find_documents forces `.sort("created_at", -1)`, and these rows have no
    created_at — that sort would order the timeline arbitrarily."""
    cursor = MagicMock()
    cursor.sort.return_value = cursor
    cursor.limit.return_value = cursor
    cursor.to_list = AsyncMock(return_value=[log_doc()])
    svc.coll.find.return_value = cursor

    await svc.svc.list_logs("org-1", "user-a", case_id="proj-1", before="alog-9")

    svc.db.find_documents.assert_not_awaited()
    cursor.sort.assert_called_once_with("_id", -1)
    query = svc.coll.find.call_args.args[0]
    assert query["org_id"] == "org-1"
    assert query["case_id"] == "proj-1"
    assert query["_id"] == {"$lt": "alog-9"}


# --- route wiring -----------------------------------------------------------


def test_routes_reject_requests_without_a_token(client: TestClient) -> None:
    assert client.get("/api/v2/organizations/org-1/activity-logs").status_code == 401
    assert (
        client.get("/api/v2/organizations/org-1/activity-logs/alog-1").status_code
        == 401
    )


def test_the_router_exposes_only_reads() -> None:
    """Nothing writes or edits history through the API."""
    methods: set[str] = set()
    for route in logs_api.router.routes:
        methods |= getattr(route, "methods", set())
    assert methods == {"GET"}


def test_list_route_passes_filters_and_the_token_subject(
    jwt_settings: None, monkeypatch: pytest.MonkeyPatch, client: TestClient
) -> None:
    lister = AsyncMock(return_value=[log_doc()])
    monkeypatch.setattr(logs_api.log_service, "list_logs", lister)

    response = client.get(
        "/api/v2/organizations/org-1/activity-logs?case_id=proj-1&limit=50",
        headers=auth("user-a"),
    )

    assert response.status_code == 200
    body = response.json()
    assert body["logs"][0]["log_id"] == "alog-1"
    assert body["logs"][0]["actor"]["role"] == "admin"
    lister.assert_awaited_once_with(
        "org-1", "user-a", case_id="proj-1", task_id=None, limit=50, before=None
    )


def test_a_short_page_offers_no_cursor(
    jwt_settings: None, monkeypatch: pytest.MonkeyPatch, client: TestClient
) -> None:
    monkeypatch.setattr(
        logs_api.log_service, "list_logs", AsyncMock(return_value=[log_doc()])
    )

    response = client.get(
        "/api/v2/organizations/org-1/activity-logs?limit=50", headers=auth("user-a")
    )

    assert response.json()["next_before"] is None


def test_a_full_page_offers_the_last_id_as_the_cursor(
    jwt_settings: None, monkeypatch: pytest.MonkeyPatch, client: TestClient
) -> None:
    monkeypatch.setattr(
        logs_api.log_service,
        "list_logs",
        AsyncMock(return_value=[log_doc(_id="alog-2"), log_doc(_id="alog-1")]),
    )

    response = client.get(
        "/api/v2/organizations/org-1/activity-logs?limit=2", headers=auth("user-a")
    )

    assert response.json()["next_before"] == "alog-1"


def test_limit_is_clamped(
    jwt_settings: None, monkeypatch: pytest.MonkeyPatch, client: TestClient
) -> None:
    lister = AsyncMock(return_value=[])
    monkeypatch.setattr(logs_api.log_service, "list_logs", lister)

    client.get(
        "/api/v2/organizations/org-1/activity-logs?limit=9999", headers=auth("user-a")
    )

    call = lister.await_args
    assert call is not None
    assert call.kwargs["limit"] == 200
