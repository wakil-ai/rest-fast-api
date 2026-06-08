"""Tests for pool-tier remaining credits derived from creditusage."""

import time
from unittest.mock import AsyncMock, MagicMock

import pytest

from services.rate_limit_service import RateLimitService

_NOW_MS = int(time.time() * 1000)
_START_MS = _NOW_MS - 5 * 24 * 60 * 60 * 1000
_END_MS = _NOW_MS + 25 * 24 * 60 * 60 * 1000


def _make_service(*, aggregate_total: int = 0) -> RateLimitService:
    service = RateLimitService.__new__(RateLimitService)
    service.subscription_storage = AsyncMock()

    aggregate_cursor = MagicMock()
    aggregate_cursor.to_list = AsyncMock(
        return_value=[{"total": aggregate_total}] if aggregate_total else []
    )
    collection = MagicMock()
    collection.aggregate.return_value = aggregate_cursor

    service.mongo_handler = MagicMock()
    service.mongo_handler.db = {RateLimitService.RATE_LIMIT_COLLECTION: collection}
    return service


@pytest.mark.asyncio
async def test_pool_remaining_uses_creditusage_over_subscription_window():
    service = _make_service(aggregate_total=1500)
    sub = {
        "user_id": "user-1",
        "tier": "standard",
        "total_credits": 6000,
        "credits_remaining": 6000,
        "start_ms": _START_MS,
        "end_ms": _END_MS,
    }

    remaining = await service._get_pool_credits_remaining("user-1", sub)

    assert remaining == 4500
    collection = service.mongo_handler.db[RateLimitService.RATE_LIMIT_COLLECTION]
    pipeline = collection.aggregate.call_args.args[0]
    assert pipeline[0]["$match"]["date"]["$gte"] == service._ms_to_date(_START_MS)
    assert pipeline[0]["$match"]["date"]["$lte"] == service._ms_to_date(_END_MS)


@pytest.mark.asyncio
async def test_pool_remaining_honors_legacy_subscription_decrements():
    service = _make_service(aggregate_total=100)
    sub = {
        "user_id": "user-1",
        "tier": "pro",
        "total_credits": 12000,
        "credits_remaining": 10000,
        "start_ms": _START_MS,
        "end_ms": _END_MS,
    }

    remaining = await service._get_pool_credits_remaining("user-1", sub)

    assert remaining == 10000


@pytest.mark.asyncio
async def test_active_pool_subscription_requires_creditusage_remaining():
    service = _make_service(aggregate_total=6000)
    service.subscription_storage.get_subscription = AsyncMock(
        return_value={
            "user_id": "user-1",
            "tier": "standard",
            "total_credits": 6000,
            "credits_remaining": 0,
            "start_ms": _START_MS,
            "end_ms": _END_MS,
        }
    )

    assert await service._get_active_pool_subscription("user-1") is None
