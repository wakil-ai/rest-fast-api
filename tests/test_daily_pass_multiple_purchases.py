import time
from unittest.mock import AsyncMock, MagicMock

import pytest

from core.config import settings
from models.payment import SubscriptionEligibilityError
from services.payments.base import BasePaymentService
from services.rate_limit_service import RateLimitService
from services.subscription_storage import SubscriptionStorage


_NOW_MS = int(time.time() * 1000)
_DAY_MS = 24 * 60 * 60 * 1000


@pytest.fixture(autouse=True)
def _daily_passes_on_sale():
    """This file covers daily-pass purchase mechanics, so the product switch is
    held on — otherwise every case would stop at DAILY_PASS_DISABLED."""
    original = settings.DAILY_PASS_ENABLED
    settings.DAILY_PASS_ENABLED = True
    yield
    settings.DAILY_PASS_ENABLED = original


def _daily_quote(*, tier: str = "basic", credits: int = 200) -> dict:
    return {
        "tier": tier,
        "period": "daily",
        "daily_credits": credits,
        "days": 1,
        "total_credits": credits,
        "amount_sum": 15000,
    }


class _Cursor:
    def __init__(self, docs: list[dict]):
        self.docs = docs

    def sort(self, field: str, direction: int):
        reverse = direction < 0
        self.docs = sorted(self.docs, key=lambda doc: doc.get(field, 0), reverse=reverse)
        return self

    async def to_list(self, length: int):
        return self.docs[:length]


class _DailyLotCollection:
    def __init__(self, docs: list[dict]):
        self.docs = docs
        self.update_calls: list[tuple[dict, dict]] = []

    def find(self, query: dict):
        docs = []
        for doc in self.docs:
            if doc["user_id"] != query["user_id"]:
                continue
            if doc["end_ms"] <= query["end_ms"]["$gt"]:
                continue
            if "credits_remaining" in doc and doc["credits_remaining"] > 0:
                docs.append(doc)
                continue
            if "credits_remaining" not in doc and doc.get("daily_credits", 0) > 0:
                docs.append(doc)
        return _Cursor(docs)

    async def find_one(self, query: dict):
        for doc in self.docs:
            if all(doc.get(key) == value for key, value in query.items()):
                return doc
        return None

    async def update_one(self, query: dict, update: dict, upsert: bool = False):
        self.update_calls.append((query, update))

    async def find_one_and_update(self, query: dict, update: dict, return_document=True):
        for doc in self.docs:
            if doc["_id"] != query["_id"] or doc["user_id"] != query["user_id"]:
                continue
            if doc["end_ms"] <= query["end_ms"]["$gt"]:
                continue
            if "$gte" in query.get("credits_remaining", {}) and (
                doc.get("credits_remaining", 0) < query["credits_remaining"]["$gte"]
            ):
                continue
            if query.get("credits_remaining", {}).get("$exists") is False and (
                "credits_remaining" in doc
                or doc.get("daily_credits", 0) < query["daily_credits"]["$gte"]
            ):
                continue
            if "$inc" in update:
                doc["credits_remaining"] += update["$inc"]["credits_remaining"]
            doc.update(update.get("$set", {}))
            return doc
        return None


def _make_storage_with_lots(lots: list[dict]) -> SubscriptionStorage:
    storage = SubscriptionStorage.__new__(SubscriptionStorage)
    storage.ensure_indexes = AsyncMock()
    storage.daily_subscriptions_collection = "daily_subscriptions"
    storage.subscriptions_collection = "subscriptions"
    storage.mongo_handler = MagicMock()
    storage.mongo_handler.db = {
        "daily_subscriptions": _DailyLotCollection(lots),
    }
    return storage


@pytest.mark.asyncio
async def test_daily_purchases_create_separate_expiring_lots():
    storage = SubscriptionStorage.__new__(SubscriptionStorage)
    storage.ensure_indexes = AsyncMock()
    storage.daily_subscriptions_collection = "daily_subscriptions"
    storage.subscriptions_collection = "subscriptions"

    daily_collection = _DailyLotCollection([])
    storage.mongo_handler = MagicMock()
    storage.mongo_handler.db = {"daily_subscriptions": daily_collection}

    first = await storage.upsert_subscription(
        user_id="user-1",
        quote=_daily_quote(credits=200),
        order_id="order-1",
        transaction_id="tx-1",
        now_ms=_NOW_MS,
        provider="test",
    )
    second = await storage.upsert_subscription(
        user_id="user-1",
        quote=_daily_quote(tier="standard", credits=500),
        order_id="order-2",
        transaction_id="tx-2",
        now_ms=_NOW_MS + 3 * 60 * 60 * 1000,
        provider="test",
    )

    assert first["credits_remaining"] == 200
    assert first["end_ms"] == _NOW_MS + _DAY_MS
    assert second["credits_remaining"] == 500
    assert second["end_ms"] == _NOW_MS + 3 * 60 * 60 * 1000 + _DAY_MS
    assert daily_collection.update_calls[0][0] == {
        "provider": "test",
        "order_id": "order-1",
    }
    assert daily_collection.update_calls[1][0] == {
        "provider": "test",
        "order_id": "order-2",
    }


@pytest.mark.asyncio
async def test_daily_purchase_retry_returns_existing_lot_without_resetting_credits():
    storage = _make_storage_with_lots(
        [
            {
                "_id": "lot-1",
                "user_id": "user-1",
                "provider": "test",
                "order_id": "order-1",
                "daily_credits": 200,
                "credits_remaining": 120,
                "total_credits": 200,
                "end_ms": _NOW_MS + _DAY_MS,
            }
        ]
    )

    document = await storage.upsert_subscription(
        user_id="user-1",
        quote=_daily_quote(credits=200),
        order_id="order-1",
        transaction_id="tx-1",
        now_ms=_NOW_MS,
        provider="test",
    )

    assert document["credits_remaining"] == 120
    assert storage.mongo_handler.db["daily_subscriptions"].update_calls == []


@pytest.mark.asyncio
async def test_same_tier_active_daily_pass_remains_eligible_for_another_purchase():
    service = BasePaymentService.__new__(BasePaymentService)
    service.subscription_storage = MagicMock()
    service.subscription_storage.get_daily_subscription = AsyncMock(
        return_value={
            "tier": "basic",
            "period": "daily",
            "daily_credits": 200,
            "credits_remaining": 200,
            "end_ms": _NOW_MS + _DAY_MS,
        }
    )

    try:
        await service.validate_subscription_eligibility(
            user_id="user-1",
            quote=_daily_quote(credits=200),
            now_ms=_NOW_MS,
        )
    except SubscriptionEligibilityError as exc:
        pytest.fail(
            "same-tier daily pass should be allowed to create another lot, "
            f"but eligibility raised {exc.code}"
        )


@pytest.mark.asyncio
async def test_daily_pass_consumes_earliest_expiring_lot_first():
    storage = _make_storage_with_lots(
        [
            {
                "_id": "later",
                "user_id": "user-1",
                "credits_remaining": 50,
                "total_credits": 50,
                "end_ms": _NOW_MS + 2 * _DAY_MS,
            },
            {
                "_id": "earlier",
                "user_id": "user-1",
                "credits_remaining": 15,
                "total_credits": 15,
                "end_ms": _NOW_MS + _DAY_MS,
            },
        ]
    )

    result = await storage.try_consume_daily_pass_credits("user-1", 20, _NOW_MS)

    docs = storage.mongo_handler.db["daily_subscriptions"].docs
    assert result["credits_remaining"] == 45
    assert next(doc for doc in docs if doc["_id"] == "earlier")[
        "credits_remaining"
    ] == 0
    assert next(doc for doc in docs if doc["_id"] == "later")[
        "credits_remaining"
    ] == 45


@pytest.mark.asyncio
async def test_daily_pass_ignores_expired_lots():
    storage = _make_storage_with_lots(
        [
            {
                "_id": "expired",
                "user_id": "user-1",
                "credits_remaining": 100,
                "total_credits": 100,
                "end_ms": _NOW_MS - 1,
            },
            {
                "_id": "active",
                "user_id": "user-1",
                "credits_remaining": 30,
                "total_credits": 30,
                "end_ms": _NOW_MS + _DAY_MS,
            },
        ]
    )

    summary = await storage.get_daily_pass_credit_summary("user-1", _NOW_MS)

    assert summary["remaining"] == 30
    assert summary["total"] == 30
    assert summary["active_lot_count"] == 1


@pytest.mark.asyncio
async def test_legacy_daily_lot_without_remaining_is_read_and_initialized_on_spend():
    storage = _make_storage_with_lots(
        [
            {
                "_id": "legacy",
                "user_id": "user-1",
                "daily_credits": 200,
                "total_credits": 200,
                "end_ms": _NOW_MS + _DAY_MS,
            },
        ]
    )

    summary = await storage.get_daily_pass_credit_summary("user-1", _NOW_MS)
    result = await storage.try_consume_daily_pass_credits("user-1", 10, _NOW_MS)

    legacy = storage.mongo_handler.db["daily_subscriptions"].docs[0]
    assert summary["remaining"] == 200
    assert result["credits_remaining"] == 190
    assert legacy["credits_remaining"] == 190


@pytest.mark.asyncio
async def test_daily_pass_spend_does_not_reset_or_block_on_calendar_usage():
    service = RateLimitService.__new__(RateLimitService)
    service.subscription_storage = AsyncMock()
    service.subscription_storage.get_subscription = AsyncMock(return_value=None)
    service.subscription_storage.get_daily_pass_credit_summary = AsyncMock(
        return_value={
            "active": True,
            "remaining": 20,
            "total": 20,
            "nearest_end_ms": _NOW_MS + _DAY_MS,
            "latest_end_ms": _NOW_MS + _DAY_MS,
            "tier": "basic",
        }
    )
    service.subscription_storage.try_consume_daily_pass_credits = AsyncMock(
        return_value={"credits_remaining": 10, "total_credits": 20}
    )
    service.promo_code_service = AsyncMock()
    service.promo_code_service.get_user_promo_status = AsyncMock(
        return_value=(False, None)
    )

    users = MagicMock()
    users.find_one = AsyncMock(return_value={"_id": "user-1"})
    creditusage = MagicMock()
    creditusage.find_one = AsyncMock(
        return_value={"credits_used": settings.DAILY_CREDITS_LIMIT}
    )
    service.mongo_handler = MagicMock()
    service.mongo_handler.db = {
        settings.USERS_COLLECTION: users,
        RateLimitService.RATE_LIMIT_COLLECTION: creditusage,
    }

    allowed, remaining, limit, _ = await service.check_and_decrement_credits(
        "user-1", "main"
    )

    assert allowed is True
    assert remaining == 10
    assert limit == 20
    creditusage.update_one.assert_not_called()
