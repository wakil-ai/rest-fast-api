"""``get_credit_status`` must surface a pure ``on_signup_bonus`` signal.

The subscription endpoint derives ``can_upload`` from the credit-status dict it
already holds, so the dict has to carry whether the user is still on their
one-time welcome pool — computed purely from ``_is_on_signup_bonus`` (the same
rule the upload gate uses), independent of daily-pass/promo precedence.
"""

import time
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock

import pytest

from core.config import settings
from services.rate_limit_service import RateLimitService

_NOW_MS = int(time.time() * 1000)
_FUTURE_MS = _NOW_MS + 24 * 60 * 60 * 1000


def _make_service(*, user, subscription=None, daily_pass=None, promo=(False, None)):
    service = RateLimitService.__new__(RateLimitService)

    service.subscription_storage = AsyncMock()
    service.subscription_storage.get_subscription = AsyncMock(return_value=subscription)
    service.subscription_storage.get_daily_subscription = AsyncMock(
        return_value=daily_pass
    )

    service.promo_code_service = AsyncMock()
    service.promo_code_service.get_user_promo_status = AsyncMock(return_value=promo)

    users = MagicMock()
    users.find_one = AsyncMock(return_value=user)

    creditusage = MagicMock()
    creditusage.find_one = AsyncMock(return_value=None)

    service.mongo_handler = MagicMock()
    service.mongo_handler.db = {
        settings.USERS_COLLECTION: users,
        RateLimitService.RATE_LIMIT_COLLECTION: creditusage,
    }
    return service


@pytest.mark.asyncio
async def test_credit_status_flags_signup_bonus_user():
    service = _make_service(
        user={
            "_id": "u1",
            "created_at": datetime.now(timezone.utc),
            "signup_credits_used": 10,
        },
    )
    status = await service.get_credit_status("u1")
    assert status["on_signup_bonus"] is True


@pytest.mark.asyncio
async def test_credit_status_signup_bonus_false_when_exhausted():
    service = _make_service(
        user={
            "_id": "u1",
            "created_at": datetime.now(timezone.utc),
            "signup_credits_used": settings.SIGNUP_DAY_CREDITS_LIMIT,
        },
    )
    status = await service.get_credit_status("u1")
    assert status["on_signup_bonus"] is False


@pytest.mark.asyncio
async def test_credit_status_signup_bonus_false_on_exhausted_flag():
    # The latch flag denies the signal even if the credit count looks unspent.
    service = _make_service(user={"_id": "u1", "signup_bonus_exhausted": True})
    status = await service.get_credit_status("u1")
    assert status["on_signup_bonus"] is False


@pytest.mark.asyncio
async def test_credit_status_signup_bonus_false_for_legacy_old_user():
    # Legacy user (no signup tracking fields) created before today is past the
    # one-day welcome window.
    service = _make_service(
        user={"_id": "u1", "created_at": datetime(2020, 1, 1, tzinfo=timezone.utc)},
    )
    status = await service.get_credit_status("u1")
    assert status["on_signup_bonus"] is False


@pytest.mark.asyncio
async def test_credit_status_signup_bonus_true_for_legacy_today_user():
    # Counterpart: a legacy user created today is still inside the window.
    service = _make_service(
        user={"_id": "u1", "created_at": datetime.now(timezone.utc)},
    )
    status = await service.get_credit_status("u1")
    assert status["on_signup_bonus"] is True


@pytest.mark.asyncio
async def test_credit_status_flags_daily_pass_with_credits():
    service = _make_service(
        user={"_id": "u1", "signup_bonus_exhausted": True},
        daily_pass={
            "tier": "basic",
            "period": "daily",
            "daily_credits": 200,
            "end_ms": _FUTURE_MS,
        },
    )
    status = await service.get_credit_status("u1")
    assert status["has_daily_pass_credits"] is True


@pytest.mark.asyncio
async def test_credit_status_exhausted_daily_pass_has_no_credits():
    # Window still open, but today's credits are spent (credits_remaining == 0).
    service = _make_service(
        user={"_id": "u1", "signup_bonus_exhausted": True},
        daily_pass={
            "tier": "basic",
            "period": "daily",
            "daily_credits": 200,
            "credits_remaining": 0,
            "end_ms": _FUTURE_MS,
        },
    )
    status = await service.get_credit_status("u1")
    assert status["has_daily_pass_credits"] is False


@pytest.mark.asyncio
async def test_credit_status_signup_bonus_pure_even_with_daily_pass():
    # Daily pass takes precedence for *credit* reporting, but the welcome-pool
    # signal must stay true so upload entitlement is computed correctly.
    service = _make_service(
        user={
            "_id": "u1",
            "created_at": datetime.now(timezone.utc),
            "signup_credits_used": 10,
        },
        daily_pass={
            "tier": "basic",
            "period": "daily",
            "daily_credits": 200,
            "end_ms": _FUTURE_MS,
        },
    )
    status = await service.get_credit_status("u1")
    assert status["on_signup_bonus"] is True
