"""Tests for paid-plan file-upload entitlement.

Covers:
  * ``RateLimitService.can_upload_files`` — free vs paid (subscription /
    daily pass / promo-unlimited) and fail-closed behaviour.
  * ``ensure_can_upload_files`` guard — raises 402 with the standardized
    machine-readable contract for free users, and blocks before file
    processing.
"""

import time
from datetime import datetime, timezone
from unittest.mock import AsyncMock

import pytest
from fastapi import HTTPException, status

from services.rate_limit_service import RateLimitService
from utils import entitlements
from utils.entitlements import (
    FILE_UPLOAD_REQUIRES_PAID_PLAN,
    ensure_can_upload_files,
)

_NOW_MS = int(time.time() * 1000)
_FUTURE_MS = _NOW_MS + 7 * 24 * 60 * 60 * 1000  # +7 days
_PAST_MS = _NOW_MS - 24 * 60 * 60 * 1000  # -1 day


def _make_service(
    *,
    subscription=None,
    daily_pass=None,
    promo=(False, None),
) -> RateLimitService:
    """Build a RateLimitService with mocked storage, bypassing DB-touching __init__."""
    service = RateLimitService.__new__(RateLimitService)
    service.mongo_handler = None  # unused by can_upload_files

    service.subscription_storage = AsyncMock()
    service.subscription_storage.get_subscription = AsyncMock(return_value=subscription)
    service.subscription_storage.get_daily_subscription = AsyncMock(
        return_value=daily_pass
    )

    service.promo_code_service = AsyncMock()
    service.promo_code_service.get_user_promo_status = AsyncMock(return_value=promo)

    # No welcome-pool user by default; signup-bonus tests override this.
    service._fetch_user = AsyncMock(return_value=None)
    return service


# --- can_upload_files ---------------------------------------------------------


async def test_active_subscription_allows_upload():
    service = _make_service(
        subscription={"tier": "pro", "end_ms": _FUTURE_MS, "credits_remaining": 200},
    )
    assert await service.can_upload_files("u1") is True


async def test_active_subscription_with_depleted_pool_still_allows_upload():
    # Upload is a tier entitlement, not a per-credit charge: a paid subscriber
    # whose pool is exhausted can still upload while the subscription is active.
    service = _make_service(
        subscription={"tier": "standard", "end_ms": _FUTURE_MS, "credits_remaining": 0},
    )
    assert await service.can_upload_files("u1") is True


async def test_active_daily_pass_allows_upload():
    service = _make_service(
        daily_pass={"daily_credits": 50, "end_ms": _FUTURE_MS},
    )
    assert await service.can_upload_files("u1") is True


async def test_unlimited_promo_allows_upload():
    # promo_credit_limit is None => unlimited.
    service = _make_service(promo=(True, None))
    assert await service.can_upload_files("u1") is True


async def test_free_user_denied():
    service = _make_service()
    assert await service.can_upload_files("u1") is False


async def test_expired_subscription_denied():
    service = _make_service(
        subscription={"tier": "pro", "end_ms": _PAST_MS, "credits_remaining": 200},
    )
    assert await service.can_upload_files("u1") is False


async def test_non_paid_tier_subscription_denied():
    # A 'daily' tier record is not a paid pool subscription (handled via daily pass).
    service = _make_service(
        subscription={"tier": "daily", "end_ms": _FUTURE_MS, "credits_remaining": 50},
    )
    assert await service.can_upload_files("u1") is False


async def test_limited_promo_does_not_grant_upload():
    # Promo with a finite credit limit is still a free-tier user, not paid.
    service = _make_service(promo=(True, 500))
    assert await service.can_upload_files("u1") is False


async def test_fails_closed_on_lookup_error():
    service = _make_service()
    service.subscription_storage.get_subscription = AsyncMock(
        side_effect=RuntimeError("db down")
    )
    assert await service.can_upload_files("u1") is False


async def test_signup_bonus_allows_upload():
    # No paid entitlement, but a fresh user still inside their first 100
    # signup credits may upload.
    service = _make_service()
    service._fetch_user = AsyncMock(return_value={"signup_credits_used": 10})
    assert await service.can_upload_files("u1") is True


async def test_exhausted_signup_bonus_denied():
    # Welcome pool spent (>= 100) and no paid plan => not entitled.
    service = _make_service()
    service._fetch_user = AsyncMock(return_value={"signup_credits_used": 100})
    assert await service.can_upload_files("u1") is False


async def test_signup_bonus_exhausted_flag_denied():
    # The latch flag (set once the pool is spent) denies upload regardless of count.
    service = _make_service()
    service._fetch_user = AsyncMock(return_value={"signup_bonus_exhausted": True})
    assert await service.can_upload_files("u1") is False


async def test_legacy_user_created_before_today_denied():
    # Legacy user (neither signup field tracked) created before today is past the
    # one-day welcome window => not on the bonus.
    service = _make_service()
    service._fetch_user = AsyncMock(
        return_value={"created_at": datetime(2020, 1, 1, tzinfo=timezone.utc)}
    )
    assert await service.can_upload_files("u1") is False


async def test_legacy_user_created_today_allowed():
    # Counterpart: a legacy user created today is still inside the welcome window.
    service = _make_service()
    service._fetch_user = AsyncMock(
        return_value={"created_at": datetime.now(timezone.utc)}
    )
    assert await service.can_upload_files("u1") is True


# --- is_upload_entitled (pure predicate) -------------------------------------


def _entitled(**overrides) -> bool:
    """Call the pure predicate with all-False defaults plus overrides."""
    service = _make_service()
    facts = dict(
        subscription=None,
        has_daily_pass_credits=False,
        unlimited_promo=False,
        on_signup_bonus=False,
    )
    facts.update(overrides)
    return service.is_upload_entitled(**facts)


def test_predicate_active_pool_subscription_allows():
    assert _entitled(subscription={"tier": "pro", "end_ms": _FUTURE_MS}) is True


def test_predicate_expired_pool_subscription_denied():
    assert _entitled(subscription={"tier": "pro", "end_ms": _PAST_MS}) is False


def test_predicate_non_pool_tier_denied():
    # A daily-tier doc is not a pool subscription; entitlement comes via the pass.
    assert _entitled(subscription={"tier": "daily", "end_ms": _FUTURE_MS}) is False


def test_predicate_active_daily_pass_allows():
    assert _entitled(has_daily_pass_credits=True) is True


def test_predicate_unlimited_promo_allows():
    assert _entitled(unlimited_promo=True) is True


def test_predicate_signup_bonus_allows():
    assert _entitled(on_signup_bonus=True) is True


def test_predicate_no_entitlement_denied():
    assert _entitled() is False


# --- ensure_can_upload_files guard -------------------------------------------


async def test_guard_allows_paid_user(monkeypatch):
    stub = AsyncMock()
    stub.can_upload_files = AsyncMock(return_value=True)
    monkeypatch.setattr(entitlements, "get_rate_limit_service", lambda: stub)

    # Should not raise.
    await ensure_can_upload_files("u1", endpoint="POST /files")


async def test_guard_denies_free_user_with_contract(monkeypatch):
    stub = AsyncMock()
    stub.can_upload_files = AsyncMock(return_value=False)
    monkeypatch.setattr(entitlements, "get_rate_limit_service", lambda: stub)

    with pytest.raises(HTTPException) as exc_info:
        await ensure_can_upload_files("u1", endpoint="POST /files")

    err = exc_info.value
    assert err.status_code == status.HTTP_402_PAYMENT_REQUIRED
    assert err.detail["code"] == FILE_UPLOAD_REQUIRES_PAID_PLAN
    assert err.detail["upgrade_required"] is True
    assert isinstance(err.detail["message"], str) and err.detail["message"]
