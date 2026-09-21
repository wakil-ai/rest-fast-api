"""A daily limit below the request cost must deny the first request of the day.

Regression: the "no creditusage bucket for today yet" branch used to insert the
bucket and return allowed unconditionally, so setting DAILY_CREDITS_LIMIT=0 in
production still let every user through exactly once per day.
"""

from unittest.mock import AsyncMock, MagicMock

import pytest

from core.assistants import AssistantConfig
from core.config import settings
from services.rate_limit_service import RateLimitService

MAIN_COST = AssistantConfig.get_credit_cost("main")


def _make_service(*, today_bucket: dict | None) -> RateLimitService:
    service = RateLimitService.__new__(RateLimitService)

    service.subscription_storage = AsyncMock()
    service.subscription_storage.get_subscription = AsyncMock(return_value=None)
    service.subscription_storage.get_daily_pass_credit_summary = AsyncMock(
        return_value={"active": False, "remaining": 0, "total": 0}
    )
    service.promo_code_service = AsyncMock()
    service.promo_code_service.get_user_promo_status = AsyncMock(
        return_value=(False, None)
    )

    users = MagicMock()
    users.find_one = AsyncMock(
        return_value={"_id": "user-1", "signup_bonus_exhausted": True}
    )

    rate_limits = MagicMock()
    rate_limits.find_one = AsyncMock(return_value=today_bucket)
    rate_limits.update_one = AsyncMock()
    rate_limits.insert_one = AsyncMock()

    service.mongo_handler = MagicMock()
    service.mongo_handler.db = {
        settings.USERS_COLLECTION: users,
        RateLimitService.RATE_LIMIT_COLLECTION: rate_limits,
    }
    return service


@pytest.mark.asyncio
async def test_zero_daily_limit_denies_first_request_of_the_day(monkeypatch):
    monkeypatch.setattr(settings, "DAILY_CREDITS_LIMIT", 0)
    service = _make_service(today_bucket=None)

    allowed, remaining, limit, _ = await service.check_and_decrement_credits(
        "user-1", "main"
    )

    assert allowed is False
    assert (remaining, limit) == (0, 0)
    rate_limits = service.mongo_handler.db[RateLimitService.RATE_LIMIT_COLLECTION]
    rate_limits.insert_one.assert_not_awaited()
    rate_limits.update_one.assert_not_awaited()


@pytest.mark.asyncio
async def test_positive_daily_limit_still_allows_first_request(monkeypatch):
    monkeypatch.setattr(settings, "DAILY_CREDITS_LIMIT", MAIN_COST * 2)
    service = _make_service(today_bucket=None)

    allowed, remaining, limit, _ = await service.check_and_decrement_credits(
        "user-1", "main"
    )

    assert allowed is True
    assert (remaining, limit) == (MAIN_COST, MAIN_COST * 2)
    rate_limits = service.mongo_handler.db[RateLimitService.RATE_LIMIT_COLLECTION]
    rate_limits.update_one.assert_awaited_once()


@pytest.mark.asyncio
async def test_error_fallback_denies_when_free_quota_cannot_cover_cost(monkeypatch):
    monkeypatch.setattr(settings, "DAILY_CREDITS_LIMIT", 0)
    service = _make_service(today_bucket=None)
    service.mongo_handler.db[settings.USERS_COLLECTION].find_one = AsyncMock(
        side_effect=RuntimeError("mongo down")
    )

    allowed, remaining, limit, _ = await service.check_and_decrement_credits(
        "user-1", "main"
    )

    assert allowed is False
    assert (remaining, limit) == (0, 0)
