"""Tests for the runtime archive guard.

``verify_not_archived`` blocks any request made on behalf of a soft-deleted
account, resolving the acting user id from path / query / JSON body, while leaving
the delete-account endpoint reachable so it stays idempotent. ``assert_not_archived``
is the explicit form used by multipart/form handlers.

The guard reads the JSON body inside a dependency; this must NOT consume the body for
the route handler (Starlette caches it). That invariant is asserted here.
"""

from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import APIRouter, Depends, FastAPI, HTTPException
from fastapi.testclient import TestClient
from pydantic import BaseModel

import security.dependencies as secdeps
from security.dependencies import assert_not_archived, verify_not_archived

ARCHIVED_ID = "ARCHIVED"


@pytest.fixture(autouse=True)
def _stub_archived_check(monkeypatch):
    """Only ``ARCHIVED_ID`` is treated as archived for these tests."""
    stub = AsyncMock()
    stub.get_user_auth_status = AsyncMock(
        side_effect=lambda uid: {
            "exists": True,
            "is_blocked": False,
            "archived": uid == ARCHIVED_ID,
        }
    )
    original = secdeps.chat_history_service
    secdeps.chat_history_service = stub
    monkeypatch.setattr(
        secdeps.redis_service, "cache_get", MagicMock(return_value=None)
    )
    monkeypatch.setattr(
        secdeps.redis_service, "cache_set", MagicMock(return_value=True)
    )
    yield
    secdeps.chat_history_service = original


def _client() -> TestClient:
    app = FastAPI()
    router = APIRouter(dependencies=[Depends(verify_not_archived)])

    class Body(BaseModel):
        user_id: str
        payload: str = "x"

    class OwnerBody(BaseModel):
        owner_id: str

    @router.post("/ask")
    async def ask(b: Body):
        return {"seen_user_id": b.user_id, "payload": b.payload}

    @router.get("/u/{user_id}")
    async def get_u(user_id: str):
        return {"ok": user_id}

    @router.get("/download")
    async def download(user_id: str):
        return {"ok": user_id}

    @router.get("/projects/{project_id}")
    async def get_project(project_id: str, owner_id: str):
        return {"ok": owner_id}

    @router.post("/proj")
    async def proj(b: OwnerBody):
        return {"ok": b.owner_id}

    @router.post("/{user_id}/delete-account")
    async def delete_account(user_id: str):
        return {"deleted": user_id}

    app.include_router(router)
    return TestClient(app)


# --- blocking across id locations --------------------------------------------
async def test_body_user_id_blocked():
    resp = _client().post("/ask", json={"user_id": ARCHIVED_ID, "payload": "hi"})
    assert resp.status_code == 403


async def test_path_user_id_blocked():
    assert _client().get(f"/u/{ARCHIVED_ID}").status_code == 403


async def test_query_user_id_blocked():
    assert _client().get(f"/download?user_id={ARCHIVED_ID}").status_code == 403


async def test_query_owner_id_blocked():
    assert _client().get(f"/projects/p1?owner_id={ARCHIVED_ID}").status_code == 403


async def test_body_owner_id_blocked():
    assert _client().post("/proj", json={"owner_id": ARCHIVED_ID}).status_code == 403


# --- allowing active users ----------------------------------------------------
async def test_active_user_allowed_and_body_still_readable():
    # The guard reads the body to extract user_id; the handler must still get it.
    resp = _client().post("/ask", json={"user_id": "alice", "payload": "hello"})
    assert resp.status_code == 200
    assert resp.json() == {"seen_user_id": "alice", "payload": "hello"}


async def test_active_path_and_query_allowed():
    c = _client()
    assert c.get("/u/bob").status_code == 200
    assert c.get("/projects/p1?owner_id=bob").status_code == 200


# --- exemptions ---------------------------------------------------------------
async def test_delete_account_route_reachable_when_archived():
    # Must stay idempotent: an archived user can still call it.
    resp = _client().post(f"/{ARCHIVED_ID}/delete-account")
    assert resp.status_code == 200
    assert resp.json() == {"deleted": ARCHIVED_ID}


# --- assert_not_archived (explicit form) -------------------------------------
async def test_assert_not_archived_raises_for_archived():
    with pytest.raises(HTTPException) as exc:
        await assert_not_archived(ARCHIVED_ID)
    assert exc.value.status_code == 403


async def test_assert_not_archived_noop_for_active_or_missing():
    await assert_not_archived("alice")  # no raise
    await assert_not_archived(None)  # no raise
