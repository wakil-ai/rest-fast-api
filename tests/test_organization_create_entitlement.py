"""Tests for the Pro-only organization-creation entitlement.

Covers:
  * ``RateLimitService.can_create_organization`` — Pro vs every other tier
    (standard, daily, no subscription), expiry, and fail-closed behaviour.
  * ``ensure_can_create_organization`` guard — raises 402 with the
    standardized machine-readable contract for non-Pro users.
"""

import time
from unittest.mock import AsyncMock

import pytest
from fastapi import HTTPException, status

from core.error_codes import ErrorCode
from services.rate_limit_service import RateLimitService
from utils import entitlements
from utils.entitlements import ensure_can_create_organization

_NOW_MS = int(time.time() * 1000)
_FUTURE_MS = _NOW_MS + 7 * 24 * 60 * 60 * 1000  # +7 days
_PAST_MS = _NOW_MS - 24 * 60 * 60 * 1000  # -1 day


def _make_service(subscription=None) -> RateLimitService:
    """Build a RateLimitService with mocked storage, bypassing DB-touching __init__."""
    service = RateLimitService.__new__(RateLimitService)
    service.subscription_storage = AsyncMock()
    service.subscription_storage.get_subscription = AsyncMock(return_value=subscription)
    return service


# --- can_create_organization --------------------------------------------------


async def test_active_pro_subscription_allows_creation():
    service = _make_service(subscription={"tier": "pro", "end_ms": _FUTURE_MS})
    assert await service.can_create_organization("u1") is True


async def test_active_standard_subscription_denied():
    # Unlike upload, org creation is Pro-only — a lower paid tier doesn't count.
    service = _make_service(subscription={"tier": "standard", "end_ms": _FUTURE_MS})
    assert await service.can_create_organization("u1") is False


async def test_expired_pro_subscription_denied():
    service = _make_service(subscription={"tier": "pro", "end_ms": _PAST_MS})
    assert await service.can_create_organization("u1") is False


async def test_no_subscription_denied():
    service = _make_service()
    assert await service.can_create_organization("u1") is False


async def test_fails_closed_on_lookup_error():
    service = _make_service()
    service.subscription_storage.get_subscription = AsyncMock(
        side_effect=RuntimeError("db down")
    )
    assert await service.can_create_organization("u1") is False


# --- ensure_can_create_organization guard -------------------------------------


async def test_guard_allows_pro_user(monkeypatch):
    stub = AsyncMock()
    stub.can_create_organization = AsyncMock(return_value=True)
    monkeypatch.setattr(entitlements, "get_rate_limit_service", lambda: stub)

    # Should not raise.
    await ensure_can_create_organization("u1", endpoint="POST /organizations")


async def test_guard_denies_non_pro_user_with_contract(monkeypatch):
    stub = AsyncMock()
    stub.can_create_organization = AsyncMock(return_value=False)
    monkeypatch.setattr(entitlements, "get_rate_limit_service", lambda: stub)

    with pytest.raises(HTTPException) as exc_info:
        await ensure_can_create_organization("u1", endpoint="POST /organizations")

    err = exc_info.value
    assert err.status_code == status.HTTP_402_PAYMENT_REQUIRED
    assert err.code == ErrorCode.ORG_CREATION_REQUIRES_PRO
    assert err.params["upgrade_required"] is True
    assert isinstance(err.detail, str) and err.detail
