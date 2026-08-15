import json
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any, NamedTuple
from unittest.mock import AsyncMock, MagicMock

import jwt
import pytest
from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient
from jwt.exceptions import InvalidTokenError

from api.v2.payment import router as payment_router
from core.config import settings
from core.dependencies import get_click_service, get_transaction_service

# Imported at module scope on purpose: importing `main` inside an async test
# constructs the app's services while an event loop is running, which schedules
# their index-init tasks and leaves them pending when the loop closes.
from main import lifespan
from security import dependencies as security_dependencies
from security import get_current_user_id
from services.jwt_service import JWTService

ACTIVE_USER = {"exists": True, "is_blocked": False, "archived": False}


@pytest.fixture
def jwt_settings(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "JWT_SECRET_KEY", "test-jwt-secret-at-least-32-bytes")
    monkeypatch.setattr(settings, "JWT_ALGORITHM", "HS256")
    monkeypatch.setattr(settings, "JWT_ISSUER", "test-issuer")
    monkeypatch.setattr(settings, "JWT_AUDIENCE", "test-audience")


class RedisMocks(NamedTuple):
    """Handles to the patched redis methods.

    Tests take these from the fixture rather than reaching back through
    ``security_dependencies.redis_service``, where the declared type is the real
    method and the patched mock is invisible to a type checker.
    """

    cache_get: MagicMock
    cache_set: MagicMock
    invalidate_cache: MagicMock


@pytest.fixture(autouse=True)
def auth_status_cache(monkeypatch: pytest.MonkeyPatch) -> RedisMocks:
    mocks = RedisMocks(
        cache_get=MagicMock(return_value=None),
        cache_set=MagicMock(return_value=True),
        invalidate_cache=MagicMock(return_value=True),
    )
    for name, mock in mocks._asdict().items():
        monkeypatch.setattr(security_dependencies.redis_service, name, mock)
    return mocks


def jwt_secret() -> str:
    """The secret the ``jwt_settings`` fixture installed.

    ``settings.JWT_SECRET_KEY`` is ``str | None``; every caller here runs under
    the fixture, so narrow it once rather than at each encode site.
    """
    secret = settings.JWT_SECRET_KEY
    assert secret is not None, "jwt_settings fixture must run before minting a token"
    return secret


def frontend_token(user_id: str = "user-123", **overrides: Any) -> str:
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
        jwt_secret(),
        algorithm=settings.JWT_ALGORITHM,
    )


def patch_user_status(
    monkeypatch: pytest.MonkeyPatch, **overrides: bool
) -> AsyncMock:
    lookup = AsyncMock(return_value={**ACTIVE_USER, **overrides})
    monkeypatch.setattr(
        security_dependencies.chat_history_service, "get_user_auth_status", lookup
    )
    return lookup


def test_verifies_frontend_signed_access_token(jwt_settings: None) -> None:
    payload = JWTService().verify(frontend_token())

    assert payload.sub == "user-123"
    assert payload.typ == "access_token"


def test_jwt_secret_is_required(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "JWT_SECRET_KEY", None)

    with pytest.raises(RuntimeError, match="JWT_SECRET_KEY"):
        JWTService()


async def test_startup_refuses_to_boot_without_a_jwt_secret(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(settings, "JWT_SECRET_KEY", None)

    with pytest.raises(RuntimeError, match="JWT_SECRET_KEY"):
        async with lifespan(FastAPI()):
            pass


async def test_startup_succeeds_with_a_valid_jwt_secret(jwt_settings: None) -> None:
    async with lifespan(FastAPI()):
        pass


def test_rejects_wrong_token_type_and_tampering(jwt_settings: None) -> None:
    service = JWTService()

    with pytest.raises(InvalidTokenError):
        service.verify(frontend_token(typ="refresh_token"))

    with pytest.raises(InvalidTokenError):
        service.verify(f"{frontend_token()}tampered")


def test_rejects_expired_or_wrong_audience_frontend_token(jwt_settings: None) -> None:
    service = JWTService()

    with pytest.raises(InvalidTokenError):
        service.verify(
            frontend_token(exp=datetime.now(timezone.utc) - timedelta(seconds=1))
        )

    with pytest.raises(InvalidTokenError):
        service.verify(frontend_token(aud="another-service"))


def test_access_token_requires_all_standard_claims(jwt_settings: None) -> None:
    service = JWTService()
    incomplete = jwt.encode(
        {
            "sub": "user-123",
            "typ": "access_token",
            "iss": settings.JWT_ISSUER,
            "aud": settings.JWT_AUDIENCE,
        },
        jwt_secret(),
        algorithm=settings.JWT_ALGORITHM,
    )

    with pytest.raises(InvalidTokenError):
        service.verify(incomplete)


def protected_app() -> TestClient:
    app = FastAPI()

    @app.get("/protected")
    async def protected(user_id: str = Depends(get_current_user_id)) -> dict[str, str]:
        return {"user_id": user_id}

    return TestClient(app)


def test_bearer_dependency_returns_existing_active_user(
    jwt_settings: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    token = frontend_token()
    patch_user_status(monkeypatch)

    response = protected_app().get(
        "/protected", headers={"Authorization": f"Bearer {token}"}
    )

    assert response.status_code == 200
    assert response.json() == {"user_id": "user-123"}


def test_bearer_dependency_rejects_a_subject_with_no_user_row(
    jwt_settings: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A validly signed token for a deleted or never-created account.

    This is the only guard against it: services take the subject on trust and do
    not re-check that it resolves to a user.
    """
    token = frontend_token("ghost")
    monkeypatch.setattr(
        security_dependencies.chat_history_service,
        "get_user_auth_status",
        AsyncMock(return_value=None),
    )

    response = protected_app().get(
        "/protected", headers={"Authorization": f"Bearer {token}"}
    )

    assert response.status_code == 401
    assert response.json()["detail"] == "User does not exist"


def test_bearer_dependency_rejects_missing_and_blocked_user(
    jwt_settings: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    token = frontend_token()
    client = protected_app()
    assert client.get("/protected").status_code == 401

    patch_user_status(monkeypatch, is_blocked=True)
    response = client.get("/protected", headers={"Authorization": f"Bearer {token}"})
    assert response.status_code == 403


def test_bearer_subject_must_match_request_user(
    jwt_settings: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    token = frontend_token()
    patch_user_status(monkeypatch)

    app = FastAPI()

    @app.get(
        "/users/{user_id}",
        dependencies=[Depends(security_dependencies.verify_user_or_service_auth)],
    )
    async def get_user(user_id: str) -> dict[str, str]:
        return {"user_id": user_id}

    client = TestClient(app)
    headers = {"Authorization": f"Bearer {token}"}

    assert client.get("/users/user-123", headers=headers).status_code == 200
    response = client.get("/users/user-456", headers=headers)
    assert response.status_code == 403
    assert (
        response.json()["detail"] == "Token subject does not match the requested user"
    )


def test_payme_init_accepts_frontend_jwt(jwt_settings, monkeypatch):
    transaction_service = MagicMock()
    transaction_service.init_payment = AsyncMock(
        return_value={
            "order_id": "order-123",
            "link": "https://checkout.paycom.uz/example",
        }
    )
    monkeypatch.setattr(
        security_dependencies.chat_history_service,
        "get_user_auth_status",
        AsyncMock(
            return_value={
                "exists": True,
                "is_blocked": False,
                "archived": False,
            }
        ),
    )

    app = FastAPI()
    app.include_router(payment_router)
    app.dependency_overrides[get_transaction_service] = lambda: transaction_service
    client = TestClient(app)

    response = client.post(
        "/transaction/payme/init",
        headers={"Authorization": f"Bearer {frontend_token()}"},
        json={
            "user_id": "user-123",
            "callback_url": "https://wakil.ai/payment-result",
            "subscription_tier": "basic",
            "subscription_period": "daily",
        },
    )

    assert response.status_code == 200
    assert response.json() == {
        "order_id": "order-123",
        "link": "https://checkout.paycom.uz/example",
    }
    transaction_service.init_payment.assert_awaited_once_with(
        amount_sum=None,
        user_id="user-123",
        callback_url="https://wakil.ai/payment-result",
        order_id=None,
        subscription_tier="basic",
        subscription_period="daily",
    )


def test_payme_init_rejects_user_id_different_from_jwt_subject(
    jwt_settings, monkeypatch
):
    transaction_service = MagicMock()
    transaction_service.init_payment = AsyncMock()
    monkeypatch.setattr(
        security_dependencies.chat_history_service,
        "get_user_auth_status",
        AsyncMock(
            return_value={
                "exists": True,
                "is_blocked": False,
                "archived": False,
            }
        ),
    )

    app = FastAPI()
    app.include_router(payment_router)
    app.dependency_overrides[get_transaction_service] = lambda: transaction_service
    client = TestClient(app)

    response = client.post(
        "/transaction/payme/init",
        headers={"Authorization": f"Bearer {frontend_token()}"},
        json={
            "user_id": "another-user",
            "callback_url": "https://wakil.ai/payment-result",
            "subscription_tier": "basic",
            "subscription_period": "daily",
        },
    )

    assert response.status_code == 403
    assert (
        response.json()["detail"]
        == "Token subject does not match the requested user"
    )
    transaction_service.init_payment.assert_not_awaited()


def test_click_init_accepts_frontend_jwt(jwt_settings, monkeypatch):
    click_service = MagicMock()
    click_service.init_payment = AsyncMock(
        return_value={
            "order_id": "order-456",
            "link": "https://my.click.uz/example",
        }
    )
    monkeypatch.setattr(
        security_dependencies.chat_history_service,
        "get_user_auth_status",
        AsyncMock(
            return_value={
                "exists": True,
                "is_blocked": False,
                "archived": False,
            }
        ),
    )

    app = FastAPI()
    app.include_router(payment_router)
    app.dependency_overrides[get_click_service] = lambda: click_service
    client = TestClient(app)

    response = client.post(
        "/transaction/click/init",
        headers={"Authorization": f"Bearer {frontend_token()}"},
        json={
            "user_id": "user-123",
            "callback_url": "https://wakil.ai/payment-result",
            "subscription_tier": "basic",
            "subscription_period": "daily",
        },
    )

    assert response.status_code == 200
    assert response.json() == {
        "order_id": "order-456",
        "link": "https://my.click.uz/example",
    }
    click_service.init_payment.assert_awaited_once()


async def test_auth_status_cache_hit_avoids_database(
    monkeypatch: pytest.MonkeyPatch, auth_status_cache: RedisMocks
) -> None:
    auth_status_cache.cache_get.return_value = json.dumps(ACTIVE_USER)
    database_lookup = AsyncMock()
    monkeypatch.setattr(
        security_dependencies.chat_history_service,
        "get_user_auth_status",
        database_lookup,
    )

    result = await security_dependencies.get_cached_user_auth_status("user-123")

    assert result == ACTIVE_USER
    database_lookup.assert_not_awaited()
    auth_status_cache.cache_set.assert_not_called()


async def test_auth_status_cache_miss_queries_and_caches_minimal_status(
    monkeypatch: pytest.MonkeyPatch, auth_status_cache: RedisMocks
) -> None:
    database_lookup = patch_user_status(monkeypatch)

    result = await security_dependencies.get_cached_user_auth_status("user-123")

    assert result == ACTIVE_USER
    database_lookup.assert_awaited_once_with("user-123")
    auth_status_cache.cache_set.assert_called_once_with(
        "auth:user-status:user-123",
        ACTIVE_USER,
        ttl_seconds=settings.AUTH_USER_STATUS_CACHE_TTL_SECONDS,
    )


async def test_malformed_cached_status_falls_back_to_database(
    monkeypatch: pytest.MonkeyPatch, auth_status_cache: RedisMocks
) -> None:
    auth_status_cache.cache_get.return_value = json.dumps(
        {"exists": "true", "is_blocked": False, "archived": False}
    )
    database_lookup = patch_user_status(monkeypatch)

    assert (
        await security_dependencies.get_cached_user_auth_status("user-123")
        == ACTIVE_USER
    )
    database_lookup.assert_awaited_once_with("user-123")


async def test_missing_user_status_is_negative_cached(
    monkeypatch: pytest.MonkeyPatch, auth_status_cache: RedisMocks
) -> None:
    monkeypatch.setattr(
        security_dependencies.chat_history_service,
        "get_user_auth_status",
        AsyncMock(return_value=None),
    )

    result = await security_dependencies.get_cached_user_auth_status("missing")

    assert result == {
        "exists": False,
        "is_blocked": False,
        "archived": False,
    }
    auth_status_cache.cache_set.assert_called_once()


def test_invalidate_user_auth_cache_uses_dedicated_key(
    auth_status_cache: RedisMocks,
) -> None:
    assert security_dependencies.invalidate_user_auth_cache("user-123") is True
    auth_status_cache.invalidate_cache.assert_called_once_with(
        "auth:user-status:user-123"
    )
