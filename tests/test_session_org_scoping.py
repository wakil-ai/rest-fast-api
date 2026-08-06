"""Organization scoping on chat sessions.

Services are built with ``__new__`` so ``__init__``'s collection/index setup never
runs; Mongo and the organization service are mocks. ``AsyncMock(unsafe=True)`` is
required for the org service because ``unittest.mock`` refuses to auto-create
attributes whose name starts with ``assert``.
"""

from datetime import datetime, timezone
from typing import Any, NamedTuple, cast
from unittest.mock import AsyncMock

import pytest
from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.testclient import TestClient

import api.v2.history.sessions as sessions_api
import services.chat_history_service as chat_history_module
import services.chat_service as chat_service_module
from core.config import settings
from models.chat import AgenticRAGRequest, ChatRequest
from security.dependencies import get_current_user_id
from services.chat_history_service import ChatHistoryService
from services.chat_service import ChatService

PERSONAL_SCOPE: dict[str, Any] = {
    "$or": [{"org_id": {"$exists": False}}, {"org_id": None}]
}
NOW = datetime.now(timezone.utc)


@pytest.fixture
def orgs(monkeypatch: pytest.MonkeyPatch) -> AsyncMock:
    """Stub organization service wired into both modules under test."""
    service = AsyncMock(unsafe=True)
    monkeypatch.setattr(
        chat_history_module, "get_organization_service", lambda: service
    )
    monkeypatch.setattr(
        chat_service_module, "get_organization_service", lambda: service
    )
    return service


class HistoryMocks(NamedTuple):
    """The service plus its Mongo mock, so assertions never reach through the
    real attribute — pyright types ``svc.db_manager`` as ``DBManager``."""

    service: ChatHistoryService
    db: AsyncMock

    def query(self) -> dict[str, Any]:
        """The filter dict from the last find_documents call — the session read.

        ``_ensure_user_exists`` queries first, so only the last call is ours.
        """
        return self.db.find_documents.await_args[0][1]

    def inserted(self) -> dict[str, Any]:
        """The first document passed to insert_documents."""
        return self.db.insert_documents.await_args[0][1][0]


def _make_history_service() -> HistoryMocks:
    svc = ChatHistoryService.__new__(ChatHistoryService)
    db = AsyncMock()
    svc.db_manager = db
    svc.sessions_collection = settings.SESSIONS_COLLECTION
    # The real _ensure_user_exists just does a find_documents, which the mock
    # answers truthily — no stub needed.
    svc.users_collection = settings.USERS_COLLECTION
    return HistoryMocks(svc, db)


def _make_chat_service(session: dict[str, Any]) -> ChatService:
    svc = ChatService.__new__(ChatService)
    history = AsyncMock()
    history.ensure_session_for_user = AsyncMock(return_value=session)
    history.create_message_id = lambda: "msg-1"
    svc.chat_history_service = history
    return svc


# Session creation


async def test_create_session_stores_org_id(orgs: AsyncMock) -> None:
    mocks = _make_history_service()

    session = await mocks.service.create_session(
        user_id="u1", title="t", org_id="org-1"
    )

    orgs.assert_org_member.assert_awaited_once_with("org-1", "u1")
    assert session["org_id"] == "org-1"
    assert mocks.inserted()["org_id"] == "org-1"


async def test_create_session_without_org_id_omits_scope(orgs: AsyncMock) -> None:
    mocks = _make_history_service()

    session = await mocks.service.create_session(user_id="u1", title="t")

    orgs.assert_org_member.assert_not_awaited()
    assert session["org_id"] is None


async def test_create_session_rejects_non_member(orgs: AsyncMock) -> None:
    mocks = _make_history_service()
    orgs.assert_org_member.side_effect = HTTPException(
        status_code=403, detail="You are not a member of this organization"
    )

    with pytest.raises(HTTPException) as exc:
        await mocks.service.create_session(user_id="u2", title="t", org_id="org-1")

    assert exc.value.status_code == 403
    mocks.db.insert_documents.assert_not_awaited()


async def test_create_session_treats_blank_org_id_as_personal(
    orgs: AsyncMock,
) -> None:
    """A blank org_id is falsy enough to skip the membership check, so if it were
    stored as-is the row would match neither the personal filter nor any org
    filter and the session would be invisible in every listing."""
    mocks = _make_history_service()

    session = await mocks.service.create_session(user_id="u1", title="t", org_id="  ")

    orgs.assert_org_member.assert_not_awaited()
    assert session["org_id"] is None
    assert mocks.inserted()["org_id"] is None


# Org inheritance onto messages and attachments
#
# Without it the account-archive sweep cannot tell a Case transcript from the
# author's personal chat, and archives the organization's evidence when that one
# member deletes their WakilAI account. `get_messages` and `get_file_by_id` both
# filter archived rows, so the Case keeps its session list while every transcript
# reads empty and every file 404s on open.


def _history_with_session(session: dict[str, Any]) -> HistoryMocks:
    mocks = _make_history_service()
    mocks.service.messages_collection = settings.MESSAGES_COLLECTION
    mocks.service.files_collection = settings.FILES_COLLECTION
    mocks.db.find_documents = AsyncMock(return_value=[session])
    mocks.db.mongo_handler.db = {settings.FILES_COLLECTION: AsyncMock()}
    return mocks


async def test_a_message_inherits_its_sessions_org() -> None:
    mocks = _history_with_session(
        {"_id": "s1", "user_id": "u1", "org_id": "org-1", "status": "active"}
    )

    message = await mocks.service.add_message("s1", None, {"text": "hi"})

    assert message["org_id"] == "org-1"
    assert mocks.inserted()["org_id"] == "org-1"


async def test_a_personal_message_carries_a_null_org() -> None:
    mocks = _history_with_session({"_id": "s1", "user_id": "u1", "status": "active"})

    message = await mocks.service.add_message("s1", None, {"text": "hi"})

    assert message["org_id"] is None


async def test_attaching_a_file_to_a_case_chat_hands_it_to_the_org() -> None:
    """A file uploaded from the personal surface becomes organization evidence the
    moment it is sent into a Case conversation — it has no session, and so no org,
    until then."""
    mocks = _history_with_session(
        {"_id": "s1", "user_id": "u1", "org_id": "org-1", "status": "active"}
    )
    mocks.service._ensure_file_attachments_for_user = AsyncMock()  # type: ignore[method-assign]

    await mocks.service.add_message("s1", ["f1", "f2"], {"text": "see attached"})

    files = mocks.db.mongo_handler.db[settings.FILES_COLLECTION]
    query, update = files.update_many.await_args.args
    assert query == {"_id": {"$in": ["f1", "f2"]}}
    assert update == {"$set": {"org_id": "org-1"}}


async def test_a_personal_chats_attachments_are_left_alone() -> None:
    """A stray org_id on a personal file would hide it from that user's own
    account deletion — the scope cuts both ways."""
    mocks = _history_with_session({"_id": "s1", "user_id": "u1", "status": "active"})
    mocks.service._ensure_file_attachments_for_user = AsyncMock()  # type: ignore[method-assign]

    await mocks.service.add_message("s1", ["f1"], {"text": "see attached"})

    files = mocks.db.mongo_handler.db[settings.FILES_COLLECTION]
    files.update_many.assert_not_awaited()


async def _upload_project_file(project: dict[str, Any] | None) -> dict[str, Any]:
    mocks = _make_history_service()
    mocks.service.files_collection = settings.FILES_COLLECTION
    mocks.service._ensure_user_exists = AsyncMock()  # type: ignore[method-assign]
    mocks.db.find_documents = AsyncMock(return_value=[])
    projects = AsyncMock()
    projects.find_one = AsyncMock(return_value=project)
    mocks.db.mongo_handler.db = {settings.PROJECTS_COLLECTION: projects}

    return await mocks.service.add_file_upload(
        user_id="u1",
        file_id="f1",
        file_url="https://x/f1",
        ocr_result="",
        file_metadata={"name": "contract.pdf"},
        status="processing",
        scope="project",
        project_id="proj-1",
    )


async def test_a_case_file_inherits_the_org_from_its_project() -> None:
    """Read here rather than passed down from the upload route: that path is long,
    every branch of it would have to remember, and one that forgot would lose the
    organization's evidence silently."""
    record = await _upload_project_file({"_id": "proj-1", "org_id": "org-1"})

    assert record["org_id"] == "org-1"


async def test_a_personal_project_file_carries_no_org() -> None:
    record = await _upload_project_file({"_id": "proj-1"})

    assert "org_id" not in record


# Read scoping
#
# These assert on the exact filter handed to Mongo rather than reimplementing a
# query matcher: the $and/$or nesting is the part that silently returns the wrong
# rows, and it is invisible to a fake that only does equality matching.


async def test_get_sessions_query_scopes_to_org_when_given(orgs: AsyncMock) -> None:
    mocks = _make_history_service()

    await mocks.service.get_sessions(user_id="u1", org_id="org-1")

    orgs.assert_org_member.assert_awaited_once_with("org-1", "u1")
    query = mocks.query()
    assert query["user_id"] == "u1"
    assert {"org_id": "org-1"} in query["$and"]
    assert PERSONAL_SCOPE not in query["$and"]
    assert "$or" not in query, "a second top-level $or would silently overwrite"


async def test_get_sessions_query_excludes_org_rows_when_personal(
    orgs: AsyncMock,
) -> None:
    mocks = _make_history_service()

    await mocks.service.get_sessions(user_id="u1")

    orgs.assert_org_member.assert_not_awaited()
    query = mocks.query()
    # Sessions predating org scoping have no org_id key, so absent must read as
    # personal — but an org row must never appear in the personal list.
    assert PERSONAL_SCOPE in query["$and"]


async def test_get_sessions_rejects_non_member(orgs: AsyncMock) -> None:
    """The read guard lives in the service, not the route, so it holds for every
    caller — a route, a script, a batch job."""
    mocks = _make_history_service()
    orgs.assert_org_member.side_effect = HTTPException(
        status_code=403, detail="You are not a member of this organization"
    )

    with pytest.raises(HTTPException) as exc:
        await mocks.service.get_sessions(user_id="u2", org_id="org-1")

    assert exc.value.status_code == 403
    mocks.db.find_documents.assert_awaited_once()  # only _ensure_user_exists ran


# Chat turn


async def test_prepare_chat_request_derives_org_from_session(orgs: AsyncMock) -> None:
    svc = _make_chat_service(
        {"_id": "ses-1", "session_id": "ses-1", "org_id": "org-1"}
    )

    session_id, message_id, project_id = await svc.prepare_chat_request(
        user_id="u1", session_id="ses-1"
    )

    # Scope comes off the stored session, never off the request.
    orgs.assert_org_member.assert_awaited_once_with("org-1", "u1")
    assert (session_id, message_id, project_id) == ("ses-1", "msg-1", None)


async def test_prepare_chat_request_skips_check_for_personal_session(
    orgs: AsyncMock,
) -> None:
    svc = _make_chat_service({"_id": "ses-1", "session_id": "ses-1", "org_id": None})

    await svc.prepare_chat_request(user_id="u1", session_id="ses-1")

    orgs.assert_org_member.assert_not_awaited()


async def test_prepare_chat_request_rejects_revoked_member(orgs: AsyncMock) -> None:
    svc = _make_chat_service(
        {"_id": "ses-1", "session_id": "ses-1", "org_id": "org-1"}
    )
    orgs.assert_org_member.side_effect = HTTPException(
        status_code=403, detail="You are not a member of this organization"
    )

    with pytest.raises(HTTPException) as exc:
        await svc.prepare_chat_request(user_id="u2", session_id="ses-1")

    assert exc.value.status_code == 403


# Direct-by-id access
#
# Scoping the list endpoint is not enough: anyone holding a session id reaches it
# through GET/PATCH/DELETE without ever listing.


async def test_assert_session_access_rejects_other_users_session(
    orgs: AsyncMock,
) -> None:
    mocks = _make_history_service()
    mocks.db.find_documents.return_value = [{"_id": "ses-1", "user_id": "u1"}]

    with pytest.raises(HTTPException) as exc:
        await mocks.service.assert_session_access("ses-1", "u2")

    assert exc.value.status_code == 403


async def test_assert_session_access_rejects_revoked_member(orgs: AsyncMock) -> None:
    mocks = _make_history_service()
    mocks.db.find_documents.return_value = [
        {"_id": "ses-1", "user_id": "u1", "org_id": "org-1"}
    ]
    orgs.assert_org_member.side_effect = HTTPException(
        status_code=403, detail="You are not a member of this organization"
    )

    with pytest.raises(HTTPException) as exc:
        await mocks.service.assert_session_access("ses-1", "u1")

    assert exc.value.status_code == 403


async def test_assert_session_access_allows_owning_member(orgs: AsyncMock) -> None:
    mocks = _make_history_service()
    mocks.db.find_documents.return_value = [
        {"_id": "ses-1", "user_id": "u1", "org_id": "org-1"}
    ]

    session = await mocks.service.assert_session_access("ses-1", "u1")

    orgs.assert_org_member.assert_awaited_once_with("org-1", "u1")
    assert session["_id"] == "ses-1"


# Credits
#
# verify_user_credits decrements and there is no refund path anywhere, so a
# request that is going to be rejected must never reach it.


async def test_chat_ask_does_not_charge_when_session_check_fails() -> None:
    svc = ChatService.__new__(ChatService)
    svc.verify_user_credits = AsyncMock()
    svc.prepare_chat_request = AsyncMock(
        side_effect=HTTPException(status_code=403, detail="not a member")
    )
    request = ChatRequest(user_id="u1", session_id="ses-1", query="hi")

    with pytest.raises(HTTPException) as exc:
        # raw_request is only read after the rejection point.
        await svc.handle_chat_ask(request, raw_request=cast(Request, None))

    assert exc.value.status_code == 403
    svc.verify_user_credits.assert_not_awaited()


async def test_agentic_rag_stream_does_not_charge_when_session_check_fails() -> None:
    svc = ChatService.__new__(ChatService)
    svc.verify_user_credits = AsyncMock()
    svc.prepare_chat_request = AsyncMock(
        side_effect=HTTPException(status_code=403, detail="not a member")
    )
    request = AgenticRAGRequest(user_id="u1", session_id="ses-1", query="hi")

    with pytest.raises(HTTPException) as exc:
        await svc.handle_agentic_rag_stream(request)

    assert exc.value.status_code == 403
    svc.verify_user_credits.assert_not_awaited()


# Router wiring
#
# The tests above prove assert_session_access works; these prove the routes
# actually call it, and that PATCH/DELETE take the actor from the token rather
# than from a request field. Without them, deleting either the guard call or the
# CurrentUser parameter leaves the whole suite green.


class Routes(NamedTuple):
    """App kept alongside the client: TestClient.app is typed as a bare ASGI app."""

    client: TestClient
    service: AsyncMock
    app: FastAPI

    def as_user(self, user_id: str) -> None:
        self.app.dependency_overrides[get_current_user_id] = lambda: user_id


@pytest.fixture
def session_routes(monkeypatch: pytest.MonkeyPatch) -> Routes:
    """Sessions router on a bare app, with the module-level service stubbed."""
    service = AsyncMock(unsafe=True)  # assert_session_access starts with "assert"
    service.assert_session_access.return_value = {
        "_id": "ses-1",
        "user_id": "u1",
        "created_at": NOW,
        "updated_at": NOW,
    }
    service.edit_session.return_value = {
        "_id": "ses-1",
        "user_id": "u1",
        "title": "renamed",
        "created_at": NOW,
        "updated_at": NOW,
    }
    monkeypatch.setattr(sessions_api, "chat_history_service", service)

    app = FastAPI()
    app.include_router(sessions_api.router)
    return Routes(TestClient(app), service, app)


async def test_patch_and_delete_require_a_token(session_routes: Routes) -> None:
    """No override, no Authorization header: the CurrentUser dependency must reject.

    These two routes carry no user_id in the path, query or body, so the
    router-level actor cross-check has nothing to compare — this is the only guard.
    """
    client, service, _ = session_routes

    assert client.patch("/sessions/ses-1", json={"title": "x"}).status_code == 401
    assert client.delete("/sessions/ses-1").status_code == 401
    service.assert_session_access.assert_not_awaited()


async def test_patch_checks_access_as_the_token_subject(session_routes: Routes) -> None:
    client, service, _ = session_routes
    session_routes.as_user("u1")

    response = client.patch("/sessions/ses-1", json={"title": "renamed"})

    assert response.status_code == 200
    service.assert_session_access.assert_awaited_once_with("ses-1", "u1")


async def test_delete_checks_access_before_deleting(session_routes: Routes) -> None:
    client, service, _ = session_routes
    session_routes.as_user("u1")
    service.assert_session_access.side_effect = HTTPException(
        status_code=403, detail="Access denied"
    )

    response = client.delete("/sessions/ses-1")

    assert response.status_code == 403
    service.delete_session.assert_not_awaited()


async def test_get_session_checks_access(session_routes: Routes) -> None:
    client, service, _ = session_routes

    response = client.get("/sessions/u1/ses-1")

    assert response.status_code == 200
    service.assert_session_access.assert_awaited_once_with("ses-1", "u1")


# --- messages router --------------------------------------------------------
# The messages router mounts under verify_user_or_service_auth, which establishes
# THAT a caller is authenticated and nothing about which sessions they may touch.
# These tests pin the missing half. They override current_actor rather than
# get_current_user_id: these handlers resolve the actor from request.state, so
# Routes.as_user would override a dependency they never call and prove nothing.

MESSAGE_DOC: dict[str, Any] = {
    "_id": "msg-1",
    "session_id": "ses-1",
    "user_id": "u1",
    "content": {"query": "hi", "response": "hello"},
    "created_at": NOW,
    "updated_at": NOW,
}


@pytest.fixture
def message_routes(monkeypatch: pytest.MonkeyPatch) -> Routes:
    """Messages router on a bare app, with the module-level service stubbed."""
    import api.v2.history.messages as messages_api

    service = AsyncMock(unsafe=True)  # assert_session_access starts with "assert"
    service.assert_session_access.return_value = {"_id": "ses-1", "user_id": "u1"}
    service.get_messages.return_value = [MESSAGE_DOC]
    service.get_message.return_value = MESSAGE_DOC
    service.add_message.return_value = MESSAGE_DOC
    monkeypatch.setattr(messages_api, "chat_history_service", service)

    app = FastAPI()
    app.include_router(messages_api.router)
    return Routes(TestClient(app), service, app)


def _as_actor(routes: Routes, actor: str | None) -> None:
    """None means a service-key caller: authenticated, but with no user identity."""
    import api.v2.history.messages as messages_api

    routes.app.dependency_overrides[messages_api.current_actor] = lambda: actor


def _refuse_access(service: AsyncMock) -> None:
    service.assert_session_access.side_effect = HTTPException(
        status_code=403, detail="Access denied"
    )


async def test_listing_messages_asserts_session_access(message_routes: Routes) -> None:
    client, service, _ = message_routes
    _as_actor(message_routes, "u1")

    client.get("/messages/ses-1")

    service.assert_session_access.assert_awaited_once_with("ses-1", "u1")


async def test_listing_another_users_session_is_refused(message_routes: Routes) -> None:
    """The whole point: a valid token is not authorization for an arbitrary id."""
    client, service, _ = message_routes
    _as_actor(message_routes, "stranger")
    _refuse_access(service)

    response = client.get("/messages/ses-1")

    assert response.status_code == 403
    service.get_messages.assert_not_awaited()


async def test_reading_one_message_asserts_session_access(
    message_routes: Routes,
) -> None:
    client, service, _ = message_routes
    _as_actor(message_routes, "stranger")
    _refuse_access(service)

    response = client.get("/messages/ses-1/msg-1")

    assert response.status_code == 403
    service.get_message.assert_not_awaited()


async def test_posting_into_another_users_session_writes_nothing(
    message_routes: Routes,
) -> None:
    """A refused write must be refused before the insert, not after."""
    client, service, _ = message_routes
    _as_actor(message_routes, "stranger")
    _refuse_access(service)

    response = client.post(
        "/messages",
        json={
            "session_id": "ses-1",
            "content": {"query": "forged", "response": "forged"},
        },
    )

    assert response.status_code == 403
    service.add_message.assert_not_awaited()


async def test_the_session_owner_is_unaffected(message_routes: Routes) -> None:
    """All four endpoints. A guard that refuses the owner is as broken as one
    that admits a stranger, and only the stranger half is covered above."""
    client, service, _ = message_routes
    _as_actor(message_routes, "u1")
    service.share_message.return_value = SHARE_DOC

    assert client.get("/messages/ses-1").status_code == 200
    assert client.get("/messages/ses-1/msg-1").status_code == 200
    assert (
        client.post(
            "/messages",
            json={"session_id": "ses-1", "content": {"query": "hi", "response": "yo"}},
        ).status_code
        == 201
    )
    assert client.post("/messages/msg-1/share").status_code == 200


async def test_a_service_key_caller_is_not_ownership_checked(
    message_routes: Routes,
) -> None:
    """Regression guard for docs/dt-team-integration.md:210, which reads sessions
    it does not own using only x-dt-team-api-key. No bearer subject exists to
    check against; the API key is the trust boundary."""
    client, service, _ = message_routes
    _as_actor(message_routes, None)

    assert client.get("/messages/ses-1").status_code == 200
    service.assert_session_access.assert_not_awaited()


SHARE_DOC: dict[str, Any] = {
    "share_id": "share-1",
    "message_id": "msg-1",
    "url": "share/share-1",
    "created_at": NOW,
}


async def test_sharing_someone_elses_message_is_refused(
    message_routes: Routes,
) -> None:
    """The severe one: GET /share/{share_id} needs no auth, so an unguarded share
    publishes another tenant's message permanently."""
    client, service, _ = message_routes
    _as_actor(message_routes, "stranger")
    _refuse_access(service)

    response = client.post("/messages/msg-1/share")

    assert response.status_code == 403
    service.share_message.assert_not_awaited()


async def test_sharing_asserts_the_messages_own_session(
    message_routes: Routes,
) -> None:
    """Resolved from the message, not from anything the caller supplied."""
    client, service, _ = message_routes
    _as_actor(message_routes, "u1")
    service.share_message.return_value = SHARE_DOC

    client.post("/messages/msg-1/share")

    service.assert_session_access.assert_awaited_once_with("ses-1", "u1")
    assert service.share_message.await_args.kwargs["user_id"] == "u1"


async def test_sharing_a_missing_message_is_404(message_routes: Routes) -> None:
    client, service, _ = message_routes
    _as_actor(message_routes, "u1")
    service.get_message.return_value = None

    assert client.post("/messages/msg-1/share").status_code == 404
    service.share_message.assert_not_awaited()


# --- the wiring the guard depends on ----------------------------------------
# Every test above overrides current_actor, so none of them exercise the chain
# that makes it work in the real app: verify_user_or_service_auth writes the JWT
# subject to request.state, and only then does current_actor read it. Break that
# ordering, or mount this router without that dependency, and current_actor
# returns None for everyone — _assert_access becomes a no-op and the whole guard
# evaporates with the suite still green. These three pin it.


def _wired_app(monkeypatch: pytest.MonkeyPatch, service: AsyncMock) -> TestClient:
    """The messages router mounted the way main.py mounts it (:142-146)."""
    import api.v2.history.messages as messages_api
    import security.dependencies as deps

    monkeypatch.setattr(messages_api, "chat_history_service", service)
    # Only token *validation* is stubbed. verify_user_or_service_auth itself runs
    # for real — its request.state write is the thing under test.
    monkeypatch.setattr(deps, "get_current_user_id", AsyncMock(return_value="u1"))
    def _accept_any_key(request_: Request) -> bool:
        return True

    monkeypatch.setattr(deps, "verify_api_key_or_dt_key", _accept_any_key)
    monkeypatch.setattr(deps, "verify_not_archived", AsyncMock(return_value=True))

    app = FastAPI()
    app.include_router(
        messages_api.router,
        dependencies=[Depends(deps.verify_user_or_service_auth)],
    )
    return TestClient(app)


async def test_a_bearer_subject_reaches_the_ownership_check(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """End to end: header -> verify_user_or_service_auth -> request.state ->
    current_actor -> assert_session_access. No dependency_overrides anywhere."""
    service = AsyncMock(unsafe=True)
    service.assert_session_access.return_value = {"_id": "ses-1", "user_id": "u1"}
    service.get_messages.return_value = [MESSAGE_DOC]
    client = _wired_app(monkeypatch, service)

    response = client.get("/messages/ses-1", headers={"Authorization": "Bearer tok"})

    assert response.status_code == 200
    service.assert_session_access.assert_awaited_once_with("ses-1", "u1")


async def test_an_api_key_caller_reaches_no_ownership_check(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The other branch, for real: no Authorization header means no subject on
    request.state, so there is nobody to check ownership against."""
    service = AsyncMock(unsafe=True)
    service.get_messages.return_value = [MESSAGE_DOC]
    client = _wired_app(monkeypatch, service)

    response = client.get("/messages/ses-1", headers={"x-api-key": "svc"})

    assert response.status_code == 200
    service.assert_session_access.assert_not_awaited()


def test_the_history_router_is_mounted_behind_the_auth_dependency() -> None:
    """Guards against the refactor the two tests above cannot see: they mount the
    router themselves, so neither would notice main.py dropping the dependency."""
    from main import create_app
    from security.dependencies import verify_user_or_service_auth

    app = create_app()
    routes = [
        r
        for r in app.routes
        if getattr(r, "path", "") == "/api/v2/history/messages/{session_id}"
    ]

    assert routes, "messages route is not mounted at the expected path"
    calls = [d.call for d in routes[0].dependant.dependencies]  # type: ignore[attr-defined]
    assert verify_user_or_service_auth in calls
