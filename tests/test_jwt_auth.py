import json
import uuid
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock

import jwt
import pytest
from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient
from jwt.exceptions import InvalidTokenError

from api.v2.payment import router as payment_router
from core.config import settings
from core.dependencies import get_click_service, get_transaction_service
from security import get_current_user_id
from security import dependencies as security_dependencies
from services.jwt_service import JWTService


@pytest.fixture
def jwt_settings(monkeypatch):
    monkeypatch.setattr(settings, "JWT_SECRET_KEY", "test-jwt-secret-at-least-32-bytes")
    monkeypatch.setattr(settings, "JWT_ALGORITHM", "HS256")
    monkeypatch.setattr(settings, "JWT_ISSUER", "test-issuer")
    monkeypatch.setattr(settings, "JWT_AUDIENCE", "test-audience")


@pytest.fixture(autouse=True)
def auth_status_cache(monkeypatch):
    monkeypatch.setattr(
        security_dependencies.redis_service,
        "cache_get",
        MagicMock(return_value=None),
    )
    monkeypatch.setattr(
        security_dependencies.redis_service,
        "cache_set",
        MagicMock(return_value=True),
    )
    monkeypatch.setattr(
        security_dependencies.redis_service,
        "invalidate_cache",
        MagicMock(return_value=True),
    )


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
        "get_user_auth_status",
        AsyncMock(
            return_value={
                "exists": True,
                "is_blocked": True,
                "archived": False,
            }
        ),
    )
    response = client.get(
        "/protected", headers={"Authorization": f"Bearer {token}"}
    )
    assert response.status_code == 403


def test_bearer_subject_must_match_request_user(jwt_settings, monkeypatch):
    token = frontend_token()
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


def guarded_ping_client() -> TestClient:
    """Bare app whose only route sits behind ``verify_user_or_service_auth``.

    No ``user_id`` in the path, so the archive guard resolves no actor and the
    service-key path needs no user lookup.
    """
    app = FastAPI()

    @app.get(
        "/ping",
        dependencies=[Depends(security_dependencies.verify_user_or_service_auth)],
    )
    async def ping() -> dict[str, bool]:
        return {"ok": True}

    return TestClient(app)


def test_service_key_is_accepted_without_an_authorization_header(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(settings, "API_KEY", "service-key")
    monkeypatch.setattr(settings, "API_KEY_NAME", "admin")

    response = guarded_ping_client().get("/ping", headers={"admin": "service-key"})

    assert response.status_code == 200


def test_non_bearer_authorization_does_not_shadow_a_valid_service_key(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A proxy (or the docs page's own Basic auth) must not veto the service key."""
    monkeypatch.setattr(settings, "API_KEY", "service-key")
    monkeypatch.setattr(settings, "API_KEY_NAME", "admin")

    response = guarded_ping_client().get(
        "/ping",
        headers={"Authorization": "Basic dXNlcjpwYXNz", "admin": "service-key"},
    )

    assert response.status_code == 200


def test_non_bearer_authorization_without_a_key_reports_the_api_key_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(settings, "API_KEY_NAME", "admin")

    response = guarded_ping_client().get(
        "/ping", headers={"Authorization": "Basic dXNlcjpwYXNz"}
    )

    assert response.status_code == 401
    # The caller is missing a key, not a bearer token; say so.
    assert "API Key required" in response.json()["detail"]


def test_api_key_error_names_the_configured_header(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(settings, "API_KEY_NAME", "X-Custom-Key")
    monkeypatch.setattr(settings, "DT_API_KEY_NAME", "x-dt-team-api-key")

    detail = guarded_ping_client().get("/ping").json()["detail"]

    assert "x-custom-key" in detail
    assert "x-dt-team-api-key" in detail


def test_empty_bearer_token_is_still_rejected() -> None:
    response = guarded_ping_client().get("/ping", headers={"Authorization": "Bearer "})

    assert response.status_code == 401
    assert response.json()["detail"] == "Bearer token required"


def test_bearer_scheme_is_matched_case_insensitively(
    jwt_settings: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    patch_user_status(monkeypatch)
    token = frontend_token()

    response = guarded_ping_client().get(
        "/ping", headers={"Authorization": f"bearer {token}"}
    )

    assert response.status_code == 200


def test_invalid_bearer_token_is_rejected_rather_than_falling_through(
    jwt_settings: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A malformed JWT must 401, not silently retry as a service key."""
    monkeypatch.setattr(settings, "API_KEY", "service-key")
    monkeypatch.setattr(settings, "API_KEY_NAME", "admin")

    response = guarded_ping_client().get(
        "/ping",
        headers={"Authorization": "Bearer not-a-jwt", "admin": "service-key"},
    )

    assert response.status_code == 401


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


async def test_auth_status_cache_hit_avoids_database(monkeypatch):
    cached_status = {
        "exists": True,
        "is_blocked": False,
        "archived": False,
    }
    security_dependencies.redis_service.cache_get.return_value = json.dumps(
        cached_status
    )
    database_lookup = AsyncMock()
    monkeypatch.setattr(
        security_dependencies.chat_history_service,
        "get_user_auth_status",
        database_lookup,
    )

    result = await security_dependencies.get_cached_user_auth_status("user-123")

    assert result == cached_status
    database_lookup.assert_not_awaited()
    security_dependencies.redis_service.cache_set.assert_not_called()


async def test_auth_status_cache_miss_queries_and_caches_minimal_status(monkeypatch):
    database_status = {
        "exists": True,
        "is_blocked": False,
        "archived": False,
    }
    database_lookup = AsyncMock(return_value=database_status)
    monkeypatch.setattr(
        security_dependencies.chat_history_service,
        "get_user_auth_status",
        database_lookup,
    )

    result = await security_dependencies.get_cached_user_auth_status("user-123")

    assert result == database_status
    database_lookup.assert_awaited_once_with("user-123")
    security_dependencies.redis_service.cache_set.assert_called_once_with(
        "auth:user-status:user-123",
        database_status,
        ttl_seconds=settings.AUTH_USER_STATUS_CACHE_TTL_SECONDS,
    )


async def test_malformed_cached_status_falls_back_to_database(monkeypatch):
    security_dependencies.redis_service.cache_get.return_value = json.dumps(
        {
            "exists": "true",
            "is_blocked": False,
            "archived": False,
        }
    )
    database_status = {
        "exists": True,
        "is_blocked": False,
        "archived": False,
    }
    database_lookup = AsyncMock(return_value=database_status)
    monkeypatch.setattr(
        security_dependencies.chat_history_service,
        "get_user_auth_status",
        database_lookup,
    )

    assert (
        await security_dependencies.get_cached_user_auth_status("user-123")
        == database_status
    )
    database_lookup.assert_awaited_once_with("user-123")


async def test_missing_user_status_is_negative_cached(monkeypatch):
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
    security_dependencies.redis_service.cache_set.assert_called_once()


def test_invalidate_user_auth_cache_uses_dedicated_key():
    assert security_dependencies.invalidate_user_auth_cache("user-123") is True
    security_dependencies.redis_service.invalidate_cache.assert_called_once_with(
        "auth:user-status:user-123"
    )
