"""Regression tests for daily-pass users with leftover welcome credits."""

import time
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock

import pytest

from core.config import settings
from services.rate_limit_service import RateLimitService

_NOW_MS = int(time.time() * 1000)
_FUTURE_MS = _NOW_MS + 24 * 60 * 60 * 1000


def _make_service(*, rate_limit_doc=None) -> RateLimitService:
    service = RateLimitService.__new__(RateLimitService)

    service.subscription_storage = AsyncMock()
    service.subscription_storage.get_subscription = AsyncMock(return_value=None)
    service.subscription_storage.get_daily_subscription = AsyncMock(
        return_value={
            "tier": "basic",
            "period": "daily",
            "daily_credits": 200,
            "end_ms": _FUTURE_MS,
        }
    )

    service.promo_code_service = AsyncMock()
    service.promo_code_service.get_user_promo_status = AsyncMock(
        return_value=(False, None)
    )

    users = MagicMock()
    users.find_one = AsyncMock(
        return_value={
            "_id": "user-1",
            "created_at": datetime.now(timezone.utc),
            "signup_credits_used": 95,
        }
    )
    users.find_one_and_update = AsyncMock()

    creditusage = MagicMock()
    creditusage.find_one = AsyncMock(return_value=rate_limit_doc)
    creditusage.insert_one = AsyncMock()
    creditusage.update_one = AsyncMock()

    service.mongo_handler = MagicMock()
    service.mongo_handler.db = {
        settings.USERS_COLLECTION: users,
        RateLimitService.RATE_LIMIT_COLLECTION: creditusage,
    }
    return service


@pytest.mark.asyncio
async def test_daily_pass_spend_is_not_blocked_by_leftover_signup_credits():
    service = _make_service()

    allowed, remaining, limit, _ = await service.check_and_decrement_credits(
        "user-1", "main"
    )

    assert allowed is True
    assert limit == 200
    assert remaining == 200 - 10

    users = service.mongo_handler.db[settings.USERS_COLLECTION]
    creditusage = service.mongo_handler.db[RateLimitService.RATE_LIMIT_COLLECTION]
    users.find_one_and_update.assert_not_called()
    creditusage.insert_one.assert_not_called()


@pytest.mark.asyncio
async def test_credit_status_reports_daily_pass_over_leftover_signup_credits():
    service = _make_service()

    status = await service.get_credit_status("user-1")

    assert status["effective_daily_credit_limit"] == settings.DAILY_CREDITS_LIMIT + 200
    assert status["remaining_credits"] == settings.DAILY_CREDITS_LIMIT + 200
    assert status["today_credits_used"] == 0
