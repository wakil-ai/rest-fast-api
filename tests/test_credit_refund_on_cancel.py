"""Refunding a credit charge for a chat request cancelled before any content
was generated (the mobile "Stop" button hit before the first streamed token).

Covers:
  * ``check_and_decrement_credits`` tags a successful charge with which bucket
    it came from (refund_info), so a later refund reverses the right one.
  * ``refund_credits`` actually reverses each bucket kind.
"""

from unittest.mock import AsyncMock, MagicMock

import pytest

from core.assistants import AssistantConfig
from core.config import settings
from services.rate_limit_service import RateLimitService

MAIN_COST = AssistantConfig.get_credit_cost("main")


def _make_service(*, user: dict, today_bucket: dict | None = None) -> RateLimitService:
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
    users.find_one = AsyncMock(return_value=user)
    users.update_one = AsyncMock()

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
async def test_daily_promo_charge_is_tagged_for_refund(monkeypatch):
    monkeypatch.setattr(settings, "DAILY_CREDITS_LIMIT", MAIN_COST * 2)
    service = _make_service(user={"_id": "user-1", "signup_bonus_exhausted": True})

    allowed, _remaining, _limit, refund_info = await service.check_and_decrement_credits(
        "user-1", "main"
    )

    assert allowed is True
    assert refund_info == {"kind": "daily_promo"}


@pytest.mark.asyncio
async def test_refund_credits_reverses_daily_promo_charge():
    service = _make_service(user={"_id": "user-1"})

    await service.refund_credits("user-1", MAIN_COST, {"kind": "daily_promo"})

    rate_limits = service.mongo_handler.db[RateLimitService.RATE_LIMIT_COLLECTION]
    rate_limits.update_one.assert_awaited_once()
    _, update = rate_limits.update_one.await_args.args
    assert update["$inc"]["credits_used"] == -MAIN_COST


@pytest.mark.asyncio
async def test_refund_credits_reverses_signup_bonus_charge():
    service = _make_service(user={"_id": "user-1"})

    await service.refund_credits("user-1", MAIN_COST, {"kind": "signup_bonus"})

    users = service.mongo_handler.db[settings.USERS_COLLECTION]
    users.update_one.assert_awaited_once()
    query, update = users.update_one.await_args.args
    assert query == {"_id": "user-1"}
    assert update["$inc"]["signup_credits_used"] == -MAIN_COST
    assert update["$set"]["signup_bonus_exhausted"] is False


@pytest.mark.asyncio
async def test_refund_credits_reverses_pool_charge():
    service = _make_service(user={"_id": "user-1"})
    service._get_active_pool_subscription = AsyncMock(return_value=None)

    await service.refund_credits("user-1", MAIN_COST, {"kind": "pool"})

    rate_limits = service.mongo_handler.db[RateLimitService.RATE_LIMIT_COLLECTION]
    rate_limits.update_one.assert_awaited_once()
    _, update = rate_limits.update_one.await_args.args
    assert update["$inc"]["credits_used"] == -MAIN_COST


@pytest.mark.asyncio
async def test_refund_credits_reverses_daily_pass_charge_on_exact_lots():
    service = _make_service(user={"_id": "user-1"})
    service.subscription_storage.refund_daily_pass_credits = AsyncMock()

    consumed = [("lot-1", 3), ("lot-2", 2)]
    await service.refund_credits(
        "user-1", 5, {"kind": "daily_pass", "consumed": consumed}
    )

    service.subscription_storage.refund_daily_pass_credits.assert_awaited_once_with(
        consumed
    )


@pytest.mark.asyncio
async def test_refund_credits_is_a_noop_without_refund_info():
    service = _make_service(user={"_id": "user-1"})

    await service.refund_credits("user-1", MAIN_COST, None)

    rate_limits = service.mongo_handler.db[RateLimitService.RATE_LIMIT_COLLECTION]
    rate_limits.update_one.assert_not_awaited()
    users = service.mongo_handler.db[settings.USERS_COLLECTION]
    users.update_one.assert_not_awaited()
