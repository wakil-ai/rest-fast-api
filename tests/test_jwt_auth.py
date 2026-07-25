import uuid
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock

import jwt
import pytest
from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient
from jwt.exceptions import InvalidTokenError

from core.config import settings
from security import get_current_user_id
from security import dependencies as security_dependencies
from services.jwt_service import JWTService


@pytest.fixture
def jwt_settings(monkeypatch):
    monkeypatch.setattr(settings, "JWT_SECRET_KEY", "test-jwt-secret-at-least-32-bytes")
    monkeypatch.setattr(settings, "JWT_ALGORITHM", "HS256")
    monkeypatch.setattr(settings, "JWT_ISSUER", "test-issuer")
    monkeypatch.setattr(settings, "JWT_AUDIENCE", "test-audience")


def frontend_token(user_id: str = "user-123", **overrides) -> str:
    now = datetime.now(timezone.utc)
    payload = {
        "sub": user_id,
        "iat": now,
        "exp": now + timedelta(minutes=5),
        "jti": uuid.uuid4().hex,
        "typ": "access_token",
        "iss": settings.JWT_ISSUER,
        "aud": settings.JWT_AUDIENCE,
        **overrides,
    }
    return jwt.encode(
        payload,
        settings.JWT_SECRET_KEY,
        algorithm=settings.JWT_ALGORITHM,
    )


def test_verifies_frontend_signed_access_token(jwt_settings):
    payload = JWTService().verify(frontend_token())

    assert payload.sub == "user-123"
    assert payload.typ == "access_token"


def test_jwt_secret_is_required(monkeypatch):
    monkeypatch.setattr(settings, "JWT_SECRET_KEY", None)

    with pytest.raises(RuntimeError, match="JWT_SECRET_KEY"):
        JWTService()


def test_rejects_wrong_token_type_and_tampering(jwt_settings):
    service = JWTService()

    with pytest.raises(InvalidTokenError):
        service.verify(frontend_token(typ="refresh_token"))

    with pytest.raises(InvalidTokenError):
        service.verify(f"{frontend_token()}tampered")


def test_rejects_expired_or_wrong_audience_frontend_token(jwt_settings):
    service = JWTService()

    with pytest.raises(InvalidTokenError):
        service.verify(
            frontend_token(exp=datetime.now(timezone.utc) - timedelta(seconds=1))
        )

    with pytest.raises(InvalidTokenError):
        service.verify(frontend_token(aud="another-service"))


def test_access_token_requires_all_standard_claims(jwt_settings):
    service = JWTService()
    incomplete = jwt.encode(
        {
            "sub": "user-123",
            "typ": "access_token",
            "iss": settings.JWT_ISSUER,
            "aud": settings.JWT_AUDIENCE,
        },
        settings.JWT_SECRET_KEY,
        algorithm=settings.JWT_ALGORITHM,
    )

    with pytest.raises(InvalidTokenError):
        service.verify(incomplete)


def test_bearer_dependency_returns_existing_active_user(jwt_settings, monkeypatch):
    token = frontend_token()
    monkeypatch.setattr(
        security_dependencies.chat_history_service,
        "get_user",
        AsyncMock(return_value={"_id": "user-123", "is_blocked": False}),
    )

    app = FastAPI()

    @app.get("/protected")
    async def protected(user_id: str = Depends(get_current_user_id)):
        return {"user_id": user_id}

    client = TestClient(app)
    response = client.get(
        "/protected", headers={"Authorization": f"Bearer {token}"}
    )

    assert response.status_code == 200
    assert response.json() == {"user_id": "user-123"}


def test_bearer_dependency_rejects_missing_and_blocked_user(
    jwt_settings, monkeypatch
):
    token = frontend_token()
    app = FastAPI()

    @app.get("/protected")
    async def protected(user_id: str = Depends(get_current_user_id)):
        return {"user_id": user_id}

    client = TestClient(app)
    assert client.get("/protected").status_code == 401

    monkeypatch.setattr(
        security_dependencies.chat_history_service,
        "get_user",
        AsyncMock(return_value={"_id": "user-123", "is_blocked": True}),
    )
    response = client.get(
        "/protected", headers={"Authorization": f"Bearer {token}"}
    )
    assert response.status_code == 403


def test_bearer_subject_must_match_request_user(jwt_settings, monkeypatch):
    token = frontend_token()
    monkeypatch.setattr(
        security_dependencies.chat_history_service,
        "get_user",
        AsyncMock(return_value={"_id": "user-123", "is_blocked": False}),
    )

    app = FastAPI()

    @app.get(
        "/users/{user_id}",
        dependencies=[Depends(security_dependencies.verify_user_or_service_auth)],
    )
    async def get_user(user_id: str):
        return {"user_id": user_id}

    client = TestClient(app)
    headers = {"Authorization": f"Bearer {token}"}

    assert client.get("/users/user-123", headers=headers).status_code == 200
    response = client.get("/users/user-456", headers=headers)
    assert response.status_code == 403
    assert (
        response.json()["detail"]
        == "Token subject does not match the requested user"
    )
