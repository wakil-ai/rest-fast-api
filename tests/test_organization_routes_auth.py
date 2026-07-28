"""Route-level auth for the organizations router: JWT subject is the only actor."""

import uuid
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock

import jwt
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from api.v2 import organizations as organizations_api
from core.config import settings
from security import dependencies as security_dependencies


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
    # settings.JWT_SECRET_KEY is str | None; the jwt_settings fixture has set it.
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


@pytest.fixture
def client() -> TestClient:
    app = FastAPI()
    app.include_router(organizations_api.router, prefix="/api/v2")
    return TestClient(app)


def test_routes_reject_requests_without_a_bearer_token(client: TestClient) -> None:
    assert client.get("/api/v2/organizations").status_code == 401
    assert client.post("/api/v2/organizations", json={"name": "X"}).status_code == 401
    assert client.post("/api/v2/organizations/invites/oinv-1/accept").status_code == 401


def test_list_uses_the_token_subject_as_the_acting_user(
    jwt_settings: None, monkeypatch: pytest.MonkeyPatch, client: TestClient
) -> None:
    lister = AsyncMock(return_value=[])
    monkeypatch.setattr(
        organizations_api.org_service, "list_user_organizations", lister
    )

    response = client.get(
        "/api/v2/organizations",
        headers={"Authorization": f"Bearer {frontend_token('user-a')}"},
    )

    assert response.status_code == 200
    lister.assert_awaited_once_with("user-a")


def test_accept_ignores_any_user_id_in_the_body_and_uses_the_token(
    jwt_settings: None, monkeypatch: pytest.MonkeyPatch, client: TestClient
) -> None:
    joined_at = datetime.now(timezone.utc)
    accepter = AsyncMock(
        return_value={
            "org_id": "org-1",
            "user_id": "user-b",
            "membership_role": "member",
            "joined_at": joined_at,
        }
    )
    monkeypatch.setattr(organizations_api.member_service, "accept_invite", accepter)

    response = client.post(
        "/api/v2/organizations/invites/oinv-1/accept",
        # A caller trying to join on someone else's behalf: the body is not read.
        json={"user_id": "somebody-else"},
        headers={"Authorization": f"Bearer {frontend_token('user-b')}"},
    )

    assert response.status_code == 200
    accepter.assert_awaited_once_with("oinv-1", "user-b")
