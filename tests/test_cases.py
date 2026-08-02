"""Cases: org scoping, the permission rules, closure, and the separation tax.

A Case is a `projects` row marked with `org_id`. The separation tests matter most:
they are what keeps Cases out of the personal project surface, where an ordinary
owner check would let a member close or archive one behind the workflow's back.
"""

import uuid
from datetime import datetime, timedelta, timezone
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import jwt
import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient

from api.v2 import cases as cases_api
from core.config import settings
from models.projects import ProjectStatus
from models.workflow_states import StateAppliesTo, StateCategory
from security import dependencies as security_dependencies
from services.case_service import CaseService
from services.project_service import PERSONAL_SCOPE, ProjectService, is_case


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
    app.include_router(cases_api.router, prefix="/api/v2")
    return TestClient(app)


def case_doc(**overrides: Any) -> dict[str, Any]:
    now = datetime.now(timezone.utc)
    doc: dict[str, Any] = {
        "_id": "proj-1",
        "org_id": "org-1",
        "owner_id": "owner",
        "title": "Contract dispute",
        "description": None,
        "objective": None,
        "case_type": "main",
        "state_id": "wfst-draft",
        "assignee_id": "assignee",
        "status": ProjectStatus.active.value,
        "closure": {},
        "files": [],
        "metadata": {},
        "stats": {"docs": 0, "chats": 0, "reminders": 0},
        "archived": False,
        "created_at": now,
        "updated_at": now,
    }
    doc.update(overrides)
    return doc


class Svc:
    """A CaseService with organization and workflow collaborators replaced."""

    def __init__(self) -> None:
        self.svc = CaseService.__new__(CaseService)
        self.db = AsyncMock()
        # count_documents goes straight at the driver collection, not through
        # find_documents, so it needs a handle of its own.
        self.coll = AsyncMock()
        self.coll.count_documents = AsyncMock(return_value=0)
        self.db.mongo_handler.db = {"projects": self.coll}
        self.svc.db = self.db  # type: ignore[misc]
        self.svc.collection = "projects"
        self.svc.prefix = "proj-"

        self.orgs = AsyncMock(unsafe=True)
        self.orgs.assert_org_member = AsyncMock(return_value={"_id": "org-1"})
        self.orgs.assert_org_admin = AsyncMock(return_value={"_id": "org-1"})
        self.orgs.get_membership = AsyncMock(return_value={"role": "member"})

        self.states = AsyncMock(unsafe=True)
        self.states.assert_state_usable = AsyncMock(return_value={"_id": "wfst-x"})
        self.states.ensure_default_states = AsyncMock()
        self.states.default_state_for = AsyncMock(return_value={"_id": "wfst-draft"})

        # Stubbed, or the real service reaches a live Mongo from inside the tests.
        self.logs = AsyncMock(unsafe=True)
        self.tasks = AsyncMock(unsafe=True)
        self.tasks.archive_for_case = AsyncMock(return_value=0)

        self.svc._logs = lambda: self.logs  # type: ignore[method-assign]
        self.svc._orgs = lambda: self.orgs  # type: ignore[method-assign]
        self.svc._states = lambda: self.states  # type: ignore[method-assign]
        self.svc._tasks = lambda: self.tasks  # type: ignore[method-assign]

    def sent(self) -> dict[str, Any]:
        return self.db.update_documents.await_args.args[2]["$set"]

    def inserted(self) -> dict[str, Any]:
        return self.db.insert_documents.await_args.args[1][0]

    def logged(self) -> list[str]:
        return [c.kwargs["event_type"].value for c in self.logs.record.await_args_list]


@pytest.fixture
def svc() -> Svc:
    built = Svc()
    built.db.find_documents = AsyncMock(return_value=[case_doc()])
    return built


# --- dates must reach Mongo as dates ----------------------------------------


def test_a_deadline_is_stored_as_a_datetime_not_a_string() -> None:
    """BSON has no date type, and compares across types by type order rather than by
    value — so a string deadline would never match a datetime range bound, and the
    `(org_id, deadline)` index would return nothing for every reminder query."""
    from datetime import date

    from utils.user_management import clean_for_mongodb

    cleaned = clean_for_mongodb({"deadline": date(2026, 12, 31)})

    assert cleaned["deadline"] == datetime(2026, 12, 31, tzinfo=timezone.utc)


def test_cleaning_leaves_a_datetime_alone() -> None:
    """datetime is a subclass of date, so branch order matters."""
    from utils.user_management import clean_for_mongodb

    moment = datetime(2026, 12, 31, 14, 30, tzinfo=timezone.utc)

    assert clean_for_mongodb({"at": moment})["at"] == moment


# --- separation: Cases must not leak onto the personal project surface ------


def test_personal_scope_matches_absent_and_null_but_not_a_case() -> None:
    """Rows predating Cases have no org_id key at all, so absence counts too."""
    clauses = PERSONAL_SCOPE["$or"]
    assert {"org_id": {"$exists": False}} in clauses
    assert {"org_id": None} in clauses
    assert is_case({"org_id": "org-1"}) is True
    assert is_case({"org_id": None}) is False
    assert is_case({}) is False


async def test_list_projects_excludes_cases_under_an_and_wrapper() -> None:
    """Two top-level `$or` keys cannot coexist in one dict — the second wins and
    every org Case would appear in the caller's personal project list."""
    svc = ProjectService.__new__(ProjectService)
    svc.db = AsyncMock()  # type: ignore[misc]
    svc.db.find_documents = AsyncMock(return_value=[])
    svc.collection = "projects"
    svc.history = AsyncMock()
    members = AsyncMock()
    members.list_member_project_ids = AsyncMock(return_value=["proj-m"])
    svc._member_service = lambda: members  # type: ignore[method-assign]

    await svc.list_projects("user-a")

    query = svc.db.find_documents.await_args.args[1]
    assert "$or" not in query, "personal predicate overwrote the ownership predicate"
    assert query["$and"] == [
        {"$or": [{"owner_id": "user-a"}, {"_id": {"$in": ["proj-m"]}}]},
        PERSONAL_SCOPE,
    ]


async def test_assert_project_owner_hides_a_case() -> None:
    """PATCH /projects writes `status` straight from the body, so an owner check
    alone would let a member close their Case outside the closure workflow."""
    svc = ProjectService.__new__(ProjectService)
    svc.db = AsyncMock()  # type: ignore[misc]
    svc.db.find_documents = AsyncMock(return_value=[case_doc(owner_id="owner")])
    svc.collection = "projects"

    with pytest.raises(HTTPException) as exc:
        await svc.assert_project_owner("proj-1", "owner")

    assert exc.value.status_code == 404


async def test_project_invites_are_refused_for_a_case() -> None:
    """project_members is reserved for the per-Case access-control customization.

    Without this guard a Case owner could mint a project invite link, and
    `accept_invite` has no organization check at all — anyone holding the link
    would join, from outside the organization.
    """
    from services.project_member_service import ProjectMemberService

    svc = ProjectMemberService.__new__(ProjectMemberService)

    import services.project_service as ps

    class FakeProjectService:
        async def _load_project_row(self, project_id: str) -> dict[str, Any]:
            return case_doc(owner_id="owner")

        reject_if_case = staticmethod(ps.ProjectService.reject_if_case)

    original = ps.ProjectService
    ps.ProjectService = FakeProjectService  # type: ignore[assignment,misc]
    try:
        with pytest.raises(HTTPException) as exc:
            await svc._assert_invite_owner("proj-1", "owner")
    finally:
        ps.ProjectService = original  # type: ignore[assignment,misc]

    assert exc.value.status_code == 404


async def test_assert_project_access_resolves_a_case_through_org_membership() -> None:
    svc = ProjectService.__new__(ProjectService)
    svc.db = AsyncMock()  # type: ignore[misc]
    svc.db.find_documents = AsyncMock(return_value=[case_doc()])
    svc.collection = "projects"
    members = AsyncMock()
    members.is_member = AsyncMock(return_value=False)
    svc._member_service = lambda: members  # type: ignore[method-assign]

    orgs = AsyncMock(unsafe=True)
    orgs.assert_org_member = AsyncMock(return_value={"_id": "org-1"})
    import core.dependencies as deps

    original = deps.get_organization_service
    deps.get_organization_service = lambda: orgs  # type: ignore[assignment]
    try:
        row = await svc.assert_project_access("proj-1", "some-member")
    finally:
        deps.get_organization_service = original  # type: ignore[assignment]

    assert row["_id"] == "proj-1"
    orgs.assert_org_member.assert_awaited_once_with("org-1", "some-member")
    # project_members stays untouched — it is reserved for a paid feature.
    members.is_member.assert_not_awaited()


async def test_a_case_reports_a_role_to_the_member_reading_it() -> None:
    """Cases leave project_members empty, so the membership lookup can only ever
    answer None — reporting no role to someone who was just granted full access.
    The org member reading a colleague's Case is a member of it."""
    svc = ProjectService.__new__(ProjectService)
    svc.db = AsyncMock()  # type: ignore[misc]
    svc.db.find_documents = AsyncMock(return_value=[case_doc()])
    svc.collection = "projects"
    members = AsyncMock()
    members.is_member = AsyncMock(return_value=False)
    svc._member_service = lambda: members  # type: ignore[method-assign]

    assert await svc.get_membership_role("proj-1", "some-member") == "member"
    assert await svc.get_membership_role("proj-1", "owner") == "owner"
    # And it never pays for a project_members lookup that cannot match.
    members.is_member.assert_not_awaited()


async def test_assert_project_access_hides_an_archived_case() -> None:
    """An archived Case 404s on the enterprise surface, so it must 404 here too —
    otherwise its files, sessions and instructions stay live on the legacy route
    after an admin has archived it."""
    svc = ProjectService.__new__(ProjectService)
    svc.db = AsyncMock()  # type: ignore[misc]
    svc.db.find_documents = AsyncMock(return_value=[case_doc(archived=True)])
    svc.collection = "projects"

    orgs = AsyncMock(unsafe=True)
    import core.dependencies as deps

    original = deps.get_organization_service
    deps.get_organization_service = lambda: orgs  # type: ignore[assignment]
    try:
        with pytest.raises(HTTPException) as exc:
            await svc.assert_project_access("proj-1", "some-member")
    finally:
        deps.get_organization_service = original  # type: ignore[assignment]

    assert exc.value.status_code == 404
    orgs.assert_org_member.assert_not_awaited()


async def test_a_case_session_carries_the_org_id() -> None:
    """Without this the row is personal-scope, and assert_session_access only
    re-checks membership when org_id is set — so a member removed from the
    organization would keep read and write on every Case chat they had opened."""
    svc = ProjectService.__new__(ProjectService)
    svc.history = AsyncMock()  # type: ignore[misc]
    svc.history.create_session = AsyncMock(return_value={"_id": "ses-1"})
    svc.assert_project_access = AsyncMock(return_value=case_doc())  # type: ignore[method-assign]

    await svc.create_session_for_project("proj-1", "member", "Case chat", [])

    assert svc.history.create_session.await_args.kwargs["org_id"] == "org-1"


async def test_a_personal_project_session_stays_personal() -> None:
    svc = ProjectService.__new__(ProjectService)
    svc.history = AsyncMock()  # type: ignore[misc]
    svc.history.create_session = AsyncMock(return_value={"_id": "ses-1"})
    svc.assert_project_access = AsyncMock(  # type: ignore[method-assign]
        return_value=case_doc(org_id=None)
    )

    await svc.create_session_for_project("proj-1", "owner", "Chat", [])

    assert svc.history.create_session.await_args.kwargs["org_id"] is None


async def test_a_session_cannot_be_tagged_to_another_cases_task() -> None:
    svc = ProjectService.__new__(ProjectService)
    svc.history = AsyncMock()  # type: ignore[misc]
    svc.assert_project_access = AsyncMock(return_value=case_doc())  # type: ignore[method-assign]

    tasks = AsyncMock(unsafe=True)
    tasks.assert_task_in_case = AsyncMock(
        side_effect=HTTPException(status_code=400, detail="wrong Case")
    )
    import core.dependencies as deps

    original = deps.get_task_service
    deps.get_task_service = lambda: tasks  # type: ignore[assignment]
    try:
        with pytest.raises(HTTPException) as exc:
            await svc.create_session_for_project(
                "proj-1", "member", "Chat", [], task_id="task-1"
            )
    finally:
        deps.get_task_service = original  # type: ignore[assignment]

    assert exc.value.status_code == 400
    svc.history.create_session.assert_not_awaited()


async def test_account_deletion_does_not_archive_an_organizations_cases() -> None:
    """A member deleting their personal account must not erase the org's board.

    `projects` is already swept by `owner_id`, and a Case's owner_id is the member
    who opened it — so without the personal-scope predicate a Google Play account
    deletion silently archives other people's work.
    """
    from services.account_archive_service import AccountArchiveService

    # Constructed normally, so this runs against the real owned-collection map
    # rather than a copy that could drift away from it.
    svc = AccountArchiveService()

    collections: dict[str, AsyncMock] = {}

    def collection_for(name: str) -> AsyncMock:
        coll = collections.setdefault(name, AsyncMock())
        coll.update_many = AsyncMock(return_value=MagicMock(modified_count=0))
        coll.update_one = AsyncMock(return_value=MagicMock(modified_count=1))
        coll.find_one = AsyncMock(return_value={"_id": "user-a", "archived": False})
        return coll

    db = MagicMock()
    db.__getitem__.side_effect = collection_for
    svc.db_manager = MagicMock()  # type: ignore[misc]
    svc.db_manager.mongo_handler.db = db

    await svc.archive_user_account("user-a")

    query = collections[settings.PROJECTS_COLLECTION].update_many.await_args.args[0]
    assert query["$or"] == PERSONAL_SCOPE["$or"], "org Cases were in the sweep"
    assert query["owner_id"] == {"$in": ["user-a"]}


# --- create -----------------------------------------------------------------


async def test_create_requires_membership(svc: Svc) -> None:
    svc.orgs.assert_org_member = AsyncMock(
        side_effect=HTTPException(status_code=403, detail="Not a member")
    )

    with pytest.raises(HTTPException) as exc:
        await svc.svc.create_case("org-1", "outsider", {"title": "X"})

    assert exc.value.status_code == 403
    svc.db.insert_documents.assert_not_awaited()
    # A rejected action leaves no trace in the timeline.
    assert svc.logged() == []


async def test_create_stamps_org_owner_and_the_default_draft_state(svc: Svc) -> None:
    doc = await svc.svc.create_case("org-1", "member", {"title": "  Dispute  "})

    inserted = svc.inserted()
    assert inserted["org_id"] == "org-1"
    assert inserted["owner_id"] == "member"
    assert inserted["title"] == "Dispute"
    assert inserted["state_id"] == "wfst-draft"
    assert inserted["status"] == ProjectStatus.active.value
    assert inserted["_id"].startswith("proj-")
    # The project pipeline reads these; a Case must keep the same shape.
    assert inserted["files"] == [] and inserted["stats"]["docs"] == 0
    assert doc["closure"] == {}
    assert svc.logged() == ["case.created"]
    svc.states.default_state_for.assert_awaited_once_with(
        "org-1", StateAppliesTo.case, StateCategory.draft
    )


async def test_create_validates_a_supplied_state(svc: Svc) -> None:
    svc.states.assert_state_usable = AsyncMock(
        side_effect=HTTPException(status_code=400, detail="wrong board")
    )

    with pytest.raises(HTTPException) as exc:
        await svc.svc.create_case(
            "org-1", "member", {"title": "X", "state_id": "wfst-t"}
        )

    assert exc.value.status_code == 400
    svc.db.insert_documents.assert_not_awaited()


async def test_create_rejects_an_assignee_outside_the_organization(svc: Svc) -> None:
    svc.orgs.get_membership = AsyncMock(return_value=None)

    with pytest.raises(HTTPException) as exc:
        await svc.svc.create_case(
            "org-1", "member", {"title": "X", "assignee_id": "stranger"}
        )

    assert exc.value.status_code == 400
    svc.db.insert_documents.assert_not_awaited()


async def test_create_rejects_a_blank_title(svc: Svc) -> None:
    with pytest.raises(HTTPException) as exc:
        await svc.svc.create_case("org-1", "member", {"title": "   "})

    assert exc.value.status_code == 400


# --- read -------------------------------------------------------------------


async def test_list_is_scoped_to_the_organization(svc: Svc) -> None:
    await svc.svc.list_cases("org-1", "member", assignee_id="a", state_id="wfst-1")

    query = svc.db.find_documents.await_args.args[1]
    assert query["org_id"] == "org-1"
    assert query["archived"] == {"$ne": True}
    assert query["assignee_id"] == "a" and query["state_id"] == "wfst-1"


async def test_a_case_from_another_organization_is_not_found(svc: Svc) -> None:
    svc.db.find_documents = AsyncMock(return_value=[])

    with pytest.raises(HTTPException) as exc:
        await svc.svc.get_case("org-1", "proj-other", "member")

    assert exc.value.status_code == 404
    assert svc.db.find_documents.await_args.args[1]["org_id"] == "org-1"


# --- edit permissions -------------------------------------------------------


@pytest.mark.parametrize("actor", ["owner", "assignee"])
async def test_owner_and_assignee_can_edit(svc: Svc, actor: str) -> None:
    await svc.svc.update_case("org-1", "proj-1", actor, {"title": "Renamed"})

    assert svc.sent()["title"] == "Renamed"
    assert svc.sent()["updated_by"] == actor


async def test_an_admin_can_edit_any_case(svc: Svc) -> None:
    svc.orgs.get_membership = AsyncMock(return_value={"role": "admin"})

    await svc.svc.update_case("org-1", "proj-1", "the-head", {"title": "Renamed"})

    assert svc.sent()["title"] == "Renamed"


async def test_an_unrelated_member_cannot_edit(svc: Svc) -> None:
    svc.orgs.get_membership = AsyncMock(return_value={"role": "member"})

    with pytest.raises(HTTPException) as exc:
        await svc.svc.update_case("org-1", "proj-1", "bystander", {"title": "Nope"})

    assert exc.value.status_code == 403
    svc.db.update_documents.assert_not_awaited()


async def test_update_cannot_write_status_closure_or_ownership(svc: Svc) -> None:
    """Lifecycle has exactly one writer; ownership never moves."""
    await svc.svc.update_case(
        "org-1",
        "proj-1",
        "owner",
        {
            "title": "Renamed",
            "status": "closed",
            "closure": {"approved_by": "me"},
            "org_id": "org-other",
            "owner_id": "me",
            "archived": True,
        },
    )

    assert set(svc.sent()) == {"title", "updated_by", "updated_at"}


async def test_update_revalidates_the_state(svc: Svc) -> None:
    await svc.svc.update_case("org-1", "proj-1", "owner", {"state_id": "wfst-x"})

    svc.states.assert_state_usable.assert_awaited_once_with(
        "org-1", "wfst-x", StateAppliesTo.case
    )


async def test_clearing_the_state_is_refused(svc: Svc) -> None:
    """A null state_id must not slip past validation into the generic write arm.

    It would drop the Case off every board and out of count_using_state, so
    archive_state would then delete a column that still holds work.
    """
    with pytest.raises(HTTPException) as exc:
        await svc.svc.update_case("org-1", "proj-1", "owner", {"state_id": None})

    assert exc.value.status_code == 400
    svc.db.update_documents.assert_not_awaited()


async def test_a_closed_case_stays_editable(svc: Svc) -> None:
    """A client PATCHing the whole form back sends the state it just displayed.

    On a closed Case that value is the closed column, which assert_state_usable
    refuses — so validating an unchanged state_id would make every other field of a
    closed Case uneditable.
    """
    svc.db.find_documents = AsyncMock(return_value=[case_doc(state_id="wfst-done")])
    svc.states.assert_state_usable = AsyncMock(
        side_effect=HTTPException(status_code=400, detail="closed")
    )

    row = await svc.svc.update_case(
        "org-1", "proj-1", "owner", {"title": "Fixed typo", "state_id": "wfst-done"}
    )

    assert row["title"] == "Fixed typo"
    svc.states.assert_state_usable.assert_not_awaited()
    # An unchanged value is not a move, so it must not be written or logged either.
    assert "state_id" not in svc.sent()


async def test_moving_a_closed_case_to_a_different_column_is_still_checked(
    svc: Svc,
) -> None:
    """The no-op shortcut must not become a way past the validator."""
    svc.db.find_documents = AsyncMock(return_value=[case_doc(state_id="wfst-draft")])
    svc.states.assert_state_usable = AsyncMock(
        side_effect=HTTPException(status_code=400, detail="closed")
    )

    with pytest.raises(HTTPException) as exc:
        await svc.svc.update_case("org-1", "proj-1", "owner", {"state_id": "wfst-done"})

    assert exc.value.status_code == 400


async def test_the_case_indexes_stay_usable_by_the_planner(svc: Svc) -> None:
    """`$type: "string"` builds an index MongoDB then refuses to use.

    It only uses a partial index when the query is provably a subset of the filter,
    and it does not infer "is a string" from an equality match — so a $type filter
    means a COLLSCAN over every personal project in the deployment, silently.
    """
    cases = AsyncMock()
    svc.db.mongo_handler.db = {"projects": cases}

    await svc.svc.ensure_indexes()

    specs = [c.kwargs for c in cases.create_index.await_args_list]
    assert specs, "no indexes created for Cases"
    for spec in specs:
        assert spec["partialFilterExpression"] == {"org_id": {"$exists": True}}
    assert {s["name"] for s in specs} == {"case_board", "case_deadline"}


async def test_a_personal_project_is_written_without_an_org_id_key() -> None:
    """The Case indexes are partial on `org_id` existing, so an explicit null would
    put every personal project in the deployment straight back into them — all of
    the write cost the partial filter exists to avoid. Null is a value; $exists
    matches it."""
    from models.projects import ProjectCreateRequest

    svc = ProjectService.__new__(ProjectService)
    svc.db = AsyncMock()  # type: ignore[misc]
    svc.collection = "projects"
    svc.prefix = "proj-"
    svc.history = AsyncMock(unsafe=True)

    doc = await svc.create_project(ProjectCreateRequest(owner_id="u1", title="Notes"))

    assert "org_id" not in doc
    # And the discriminator still reads it as personal.
    assert not is_case(doc)


async def test_a_closed_case_cannot_be_moved_off_the_closed_column(svc: Svc) -> None:
    """assert_state_usable refuses the closed column, so a move off it is one-way:
    the Case would read as in progress while status said closed, with nothing able
    to put it back. Reopening is the way out, not a board edit."""
    svc.db.find_documents = AsyncMock(
        return_value=[
            case_doc(
                state_id="wfst-done",
                status=ProjectStatus.closed.value,
                closure={"approved_by": "head", "approved_at": "t"},
            )
        ]
    )

    with pytest.raises(HTTPException) as exc:
        await svc.svc.update_case(
            "org-1", "proj-1", "owner", {"state_id": "wfst-progress"}
        )

    assert exc.value.status_code == 409
    svc.db.update_documents.assert_not_awaited()


async def test_a_closed_case_can_still_have_its_other_fields_edited(svc: Svc) -> None:
    """Freezing the board position must not freeze the whole record."""
    svc.db.find_documents = AsyncMock(
        return_value=[
            case_doc(
                state_id="wfst-done", closure={"approved_by": "h", "approved_at": "t"}
            )
        ]
    )

    row = await svc.svc.update_case("org-1", "proj-1", "owner", {"title": "Renamed"})

    assert row["title"] == "Renamed"


async def test_reopening_clears_the_closure_and_returns_to_draft(svc: Svc) -> None:
    svc.db.find_documents = AsyncMock(
        return_value=[
            case_doc(
                state_id="wfst-done",
                status=ProjectStatus.closed.value,
                closure={"approved_by": "head", "approved_at": "t"},
            )
        ]
    )
    svc.states.default_state_for = AsyncMock(return_value={"_id": "wfst-draft"})

    await svc.svc.reopen("org-1", "proj-1", "the-head")

    sent = svc.sent()
    assert sent["status"] == ProjectStatus.active.value
    # Cleared, not amended: a leftover requested_at would let the next approval run
    # against a request nobody made.
    assert sent["closure"] == {}
    assert sent["state_id"] == "wfst-draft"
    svc.states.default_state_for.assert_awaited_once_with(
        "org-1", StateAppliesTo.case, StateCategory.draft
    )
    assert svc.logged() == ["closure.reopened"]


async def test_reopening_is_admin_only(svc: Svc) -> None:
    svc.orgs.assert_org_admin = AsyncMock(
        side_effect=HTTPException(status_code=403, detail="Admin only")
    )

    with pytest.raises(HTTPException) as exc:
        await svc.svc.reopen("org-1", "proj-1", "member")

    assert exc.value.status_code == 403
    svc.db.update_documents.assert_not_awaited()


async def test_reopening_an_open_case_is_refused(svc: Svc) -> None:
    svc.db.find_documents = AsyncMock(return_value=[case_doc(closure={})])

    with pytest.raises(HTTPException) as exc:
        await svc.svc.reopen("org-1", "proj-1", "the-head")

    assert exc.value.status_code == 409


async def test_a_reopened_case_can_be_closed_again(svc: Svc) -> None:
    """The round trip is the whole point — reopen must not be a second dead end."""
    svc.db.find_documents = AsyncMock(
        return_value=[case_doc(status=ProjectStatus.active.value, closure={})]
    )

    await svc.svc.request_closure("org-1", "proj-1", "assignee")
    svc.db.find_documents = AsyncMock(
        return_value=[case_doc(closure={"requested_by": "assignee", "requested_at": "t"})]
    )
    svc.states.default_state_for = AsyncMock(return_value={"_id": "wfst-done"})

    await svc.svc.approve_closure("org-1", "proj-1", "the-head")

    assert svc.sent()["status"] == ProjectStatus.closed.value


async def test_the_archive_event_is_written_before_the_cascade(svc: Svc) -> None:
    """A cascade that throws must not cost the evidence record.

    The log is guarded by the pre-write snapshot, so logging after the cascade would
    lose the event for good: the retry sees archived=True and skips it.
    """
    svc.tasks.archive_for_case = AsyncMock(side_effect=RuntimeError("mongo blip"))

    with pytest.raises(RuntimeError):
        await svc.svc.archive_case("org-1", "proj-1", "the-head")

    assert svc.logged() == ["case.archived"]


async def test_archiving_twice_finishes_an_interrupted_cascade(svc: Svc) -> None:
    """The Case is written before its Tasks, so a failed cascade leaves live Tasks.

    The retry has to be able to complete it — an active-only load would 404 instead.
    """
    svc.db.find_documents = AsyncMock(return_value=[case_doc(archived=True)])

    await svc.svc.archive_case("org-1", "proj-1", "the-head")

    svc.tasks.archive_for_case.assert_awaited_once_with("org-1", "proj-1", "the-head")
    # Already archived, so no second case.archived event on the timeline.
    assert svc.logged() == []
    # The load must not filter on `archived`, or the retry 404s before it can
    # finish the cascade. Asserting the query, not just the stubbed result —
    # a mock returns its value whatever it is asked for.
    assert svc.db.find_documents.await_args.args[1] == {
        "_id": "proj-1",
        "org_id": "org-1",
    }


async def test_count_using_state_counts_in_the_server(svc: Svc) -> None:
    svc.coll.count_documents = AsyncMock(return_value=873)

    assert await svc.svc.count_using_state("org-1", "wfst-1") == 873

    query = svc.coll.count_documents.await_args.args[0]
    assert query == {"org_id": "org-1", "state_id": "wfst-1", "archived": {"$ne": True}}


async def test_archiving_a_closed_case_keeps_its_closed_status(svc: Svc) -> None:
    """`archived` is the flag every read filters on; `status` is the lifecycle
    marker. Overwriting it would erase the fact that the work was signed off, and
    there is no un-archive route to recover it — `_load_case` filters archived rows,
    so `reopen` 404s. Filing a closed Case away is not undoing its closure."""
    svc.db.find_documents = AsyncMock(
        return_value=[
            case_doc(
                status=ProjectStatus.closed.value,
                closure={"approved_by": "head", "approved_at": "t"},
            )
        ]
    )

    await svc.svc.archive_case("org-1", "proj-1", "the-head")

    sent = svc.sent()
    assert sent["archived"] is True
    assert "status" not in sent


async def test_archiving_an_open_case_does_set_the_archived_status(svc: Svc) -> None:
    await svc.svc.archive_case("org-1", "proj-1", "the-head")

    assert svc.sent()["status"] == ProjectStatus.archived.value


async def test_archiving_a_case_archives_its_tasks(svc: Svc) -> None:
    """Tasks cannot outlive their parent — they would point at a Case that 404s."""
    await svc.svc.archive_case("org-1", "proj-1", "the-head")

    svc.tasks.archive_for_case.assert_awaited_once_with("org-1", "proj-1", "the-head")


async def test_archive_is_admin_only(svc: Svc) -> None:
    svc.orgs.assert_org_admin = AsyncMock(
        side_effect=HTTPException(status_code=403, detail="Admin only")
    )

    with pytest.raises(HTTPException) as exc:
        await svc.svc.archive_case("org-1", "proj-1", "member")

    assert exc.value.status_code == 403
    svc.db.update_documents.assert_not_awaited()
    svc.tasks.archive_for_case.assert_not_awaited()


# --- closure ----------------------------------------------------------------


async def test_request_closure_records_the_requester(svc: Svc) -> None:
    row = await svc.svc.request_closure("org-1", "proj-1", "assignee")

    closure = svc.sent()["closure"]
    assert svc.logged() == ["closure.requested"]
    assert closure["requested_by"] == "assignee" and closure["requested_at"]
    assert "approved_at" not in closure
    # Requesting alone must not close anything.
    assert "status" not in svc.sent()
    assert row["status"] == ProjectStatus.active.value


async def test_approve_without_a_request_is_refused(svc: Svc) -> None:
    with pytest.raises(HTTPException) as exc:
        await svc.svc.approve_closure("org-1", "proj-1", "the-head")

    assert exc.value.status_code == 409
    svc.db.update_documents.assert_not_awaited()


async def test_approve_closure_is_admin_only(svc: Svc) -> None:
    svc.orgs.assert_org_admin = AsyncMock(
        side_effect=HTTPException(status_code=403, detail="Admin only")
    )

    with pytest.raises(HTTPException) as exc:
        await svc.svc.approve_closure("org-1", "proj-1", "assignee")

    assert exc.value.status_code == 403
    svc.db.update_documents.assert_not_awaited()


async def test_approval_moves_status_and_state_in_one_write(svc: Svc) -> None:
    """The single-writer rule: the board and the lifecycle can never disagree."""
    svc.db.find_documents = AsyncMock(
        return_value=[case_doc(closure={"requested_by": "a", "requested_at": "t"})]
    )
    svc.states.default_state_for = AsyncMock(return_value={"_id": "wfst-done"})

    await svc.svc.approve_closure("org-1", "proj-1", "the-head")

    sent = svc.sent()
    assert sent["status"] == ProjectStatus.closed.value
    assert sent["state_id"] == "wfst-done"
    assert sent["closure"]["approved_by"] == "the-head"
    # Matched by category, never by the column's name — it may be renamed.
    svc.states.default_state_for.assert_awaited_once_with(
        "org-1", StateAppliesTo.case, StateCategory.closed
    )
    assert svc.logged() == ["closure.approved"]


async def test_approving_twice_is_refused(svc: Svc) -> None:
    svc.db.find_documents = AsyncMock(
        return_value=[case_doc(closure={"requested_at": "t", "approved_at": "t2"})]
    )

    with pytest.raises(HTTPException) as exc:
        await svc.svc.approve_closure("org-1", "proj-1", "the-head")

    assert exc.value.status_code == 409


async def test_closure_survives_a_renamed_done_column(svc: Svc) -> None:
    """A board whose closed column has no `closed` category still closes cleanly."""
    svc.db.find_documents = AsyncMock(
        return_value=[case_doc(closure={"requested_at": "t"})]
    )
    svc.states.default_state_for = AsyncMock(return_value=None)

    await svc.svc.approve_closure("org-1", "proj-1", "the-head")

    sent = svc.sent()
    assert sent["status"] == ProjectStatus.closed.value
    assert "state_id" not in sent


# --- route wiring -----------------------------------------------------------


def test_every_route_rejects_a_request_without_a_token(client: TestClient) -> None:
    base = "/api/v2/organizations/org-1/cases"
    assert client.get(base).status_code == 401
    assert client.post(base, json={"title": "X"}).status_code == 401
    assert client.get(f"{base}/proj-1").status_code == 401
    assert client.patch(f"{base}/proj-1", json={"title": "X"}).status_code == 401
    assert client.delete(f"{base}/proj-1").status_code == 401
    assert client.post(f"{base}/proj-1/closure/request").status_code == 401
    assert client.post(f"{base}/proj-1/closure/approve").status_code == 401


def test_create_route_uses_the_token_subject(
    jwt_settings: None, monkeypatch: pytest.MonkeyPatch, client: TestClient
) -> None:
    creator = AsyncMock(return_value=case_doc())
    monkeypatch.setattr(cases_api.case_service, "create_case", creator)

    response = client.post(
        "/api/v2/organizations/org-1/cases",
        json={"title": "Dispute", "owner_id": "somebody-else"},
        headers=auth("user-a"),
    )

    assert response.status_code == 201
    assert response.json()["case_id"] == "proj-1"
    creator.assert_awaited_once_with("org-1", "user-a", {"title": "Dispute"})


def test_create_route_hands_the_service_a_real_date(
    jwt_settings: None, monkeypatch: pytest.MonkeyPatch, client: TestClient
) -> None:
    """Not an ISO string: it has to survive as a date all the way into Mongo."""
    from datetime import date

    creator = AsyncMock(return_value=case_doc())
    monkeypatch.setattr(cases_api.case_service, "create_case", creator)

    client.post(
        "/api/v2/organizations/org-1/cases",
        json={"title": "Dispute", "deadline": "2026-12-31"},
        headers=auth("user-a"),
    )

    assert creator.await_args.args[2]["deadline"] == date(2026, 12, 31)


def test_the_list_route_refuses_an_unbounded_limit(
    jwt_settings: None, client: TestClient
) -> None:
    base = "/api/v2/organizations/org-1/cases"
    assert client.get(f"{base}?limit=1000000", headers=auth("user-a")).status_code == 422
    assert client.get(f"{base}?limit=0", headers=auth("user-a")).status_code == 422
    assert client.get(f"{base}?skip=-1", headers=auth("user-a")).status_code == 422


def test_patch_route_forwards_only_supplied_keys(
    jwt_settings: None, monkeypatch: pytest.MonkeyPatch, client: TestClient
) -> None:
    updater = AsyncMock(return_value=case_doc(title="Renamed"))
    monkeypatch.setattr(cases_api.case_service, "update_case", updater)

    response = client.patch(
        "/api/v2/organizations/org-1/cases/proj-1",
        json={"title": "Renamed"},
        headers=auth("user-a"),
    )

    assert response.status_code == 200
    updater.assert_awaited_once_with("org-1", "proj-1", "user-a", {"title": "Renamed"})


def test_closure_routes_use_the_token_subject(
    jwt_settings: None, monkeypatch: pytest.MonkeyPatch, client: TestClient
) -> None:
    req = AsyncMock(return_value=case_doc())
    app = AsyncMock(return_value=case_doc(status=ProjectStatus.closed.value))
    monkeypatch.setattr(cases_api.case_service, "request_closure", req)
    monkeypatch.setattr(cases_api.case_service, "approve_closure", app)

    base = "/api/v2/organizations/org-1/cases/proj-1/closure"
    assert client.post(f"{base}/request", headers=auth("user-a")).status_code == 200
    assert client.post(f"{base}/approve", headers=auth("head")).status_code == 200

    req.assert_awaited_once_with("org-1", "proj-1", "user-a")
    app.assert_awaited_once_with("org-1", "proj-1", "head")


def test_delete_route_archives(
    jwt_settings: None, monkeypatch: pytest.MonkeyPatch, client: TestClient
) -> None:
    archiver = AsyncMock(return_value=case_doc(archived=True))
    monkeypatch.setattr(cases_api.case_service, "archive_case", archiver)

    response = client.delete(
        "/api/v2/organizations/org-1/cases/proj-1", headers=auth("head")
    )

    assert response.status_code == 204
    archiver.assert_awaited_once_with("org-1", "proj-1", "head")


def test_create_route_rejects_an_unknown_case_type(
    jwt_settings: None, client: TestClient
) -> None:
    response = client.post(
        "/api/v2/organizations/org-1/cases",
        json={"title": "X", "case_type": "not_an_assistant"},
        headers=auth("user-a"),
    )

    assert response.status_code == 422
