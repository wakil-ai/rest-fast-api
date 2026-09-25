"""Regression coverage for the paid Standard-to-Pro upgrade policy."""

from unittest.mock import AsyncMock, MagicMock

import pytest

from services.subscription_storage import SubscriptionStorage
from services.payments.base import BasePaymentService


NOW_MS = 1_700_000_000_000
DAY_MS = 24 * 60 * 60 * 1000


def make_storage(existing_subscription: dict, upgraded_subscription: dict) -> tuple[SubscriptionStorage, AsyncMock]:
    storage = SubscriptionStorage.__new__(SubscriptionStorage)
    storage.ensure_indexes = AsyncMock()
    storage.get_subscription = AsyncMock(return_value=existing_subscription)
    storage.subscriptions_collection = "subscriptions"
    storage.daily_subscriptions_collection = "daily_subscriptions"

    collection = MagicMock()
    collection.find_one_and_update = AsyncMock(return_value=upgraded_subscription)
    collection.update_one = AsyncMock()
    storage.mongo_handler = MagicMock()
    storage.mongo_handler.db = {"subscriptions": collection}
    return storage, collection.find_one_and_update


@pytest.mark.asyncio
async def test_paid_web_pro_upgrade_carries_unused_standard_credits_into_new_period():
    """A web upgrade is full price, immediate, and never extends Standard's end date."""
    existing = {
        "user_id": "user-1",
        "tier": "standard",
        "period": "monthly",
        "provider": "payme",
        "credits_remaining": 4_000,
        "total_credits": 6_000,
        "end_ms": NOW_MS + 20 * DAY_MS,
    }
    upgraded = {
        "user_id": "user-1",
        "tier": "pro",
        "period": "monthly",
        "credits_remaining": 22_000,
        "total_credits": 22_000,
        "start_ms": NOW_MS,
        "end_ms": NOW_MS + 30 * DAY_MS,
    }
    storage, find_one_and_update = make_storage(existing, upgraded)

    result = await storage.upsert_subscription(
        user_id="user-1",
        quote={
            "tier": "pro",
            "period": "monthly",
            "days": 30,
            "daily_credits": 0,
            "total_credits": 18_000,
            "amount_sum": 300_000,
        },
        order_id="click-order-1",
        transaction_id="click-transaction-1",
        now_ms=NOW_MS,
        provider="click",
        full_price_standard_to_pro_upgrade=True,
    )

    assert result == upgraded
    query, pipeline = find_one_and_update.await_args.args[:2]
    assert query == {
        "user_id": "user-1",
        "tier": "standard",
        "end_ms": {"$gt": NOW_MS},
    }

    document = pipeline[0]["$set"]
    assert document["start_ms"] == NOW_MS
    assert document["end_ms"] == NOW_MS + 30 * DAY_MS
    assert document["upgrade_from_tier"] == "standard"
    assert document["total_credits"] == {"$add": [{"$max": [0, {"$ifNull": ["$credits_remaining", "$total_credits"]}]}, 18_000]}
    assert document["credits_remaining"] == {"$add": [{"$max": [0, {"$ifNull": ["$credits_remaining", "$total_credits"]}]}, 18_000]}


@pytest.mark.asyncio
async def test_web_pro_checkout_keeps_the_full_pro_price():
    """A Standard user's remaining paid time must never discount a Pro invoice."""
    quote = {
        "tier": "pro",
        "period": "monthly",
        "days": 30,
        "daily_credits": 0,
        "total_credits": 18_000,
        "amount_sum": 300_000,
    }
    service = BasePaymentService.__new__(BasePaymentService)
    service.ensure_invoice_indexes = AsyncMock()
    service._get_subscription_quote = MagicMock(return_value=quote)
    service.get_user_by_id = AsyncMock(return_value={"_id": "user-1"})
    service.validate_subscription_eligibility = AsyncMock()
    service._resolve_purpose = MagicMock(return_value="subscription")
    service._build_invoice_document = MagicMock(
        side_effect=lambda **kwargs: {
            "amount_sum": kwargs["amount_sum"],
            "subscription": kwargs["quote"],
        }
    )
    service.build_payment_link = AsyncMock(return_value="https://checkout.example")
    service.invoices_collection = "invoices"
    service.provider = "payme"
    service.db_handler = MagicMock()
    service.db_handler.find_one = AsyncMock(return_value=None)
    service.db_handler.insert_one = AsyncMock()

    result = await service.init_payment(
        amount_sum=None,
        user_id="user-1",
        callback_url="https://app.example/payment-result",
        order_id="order-1",
        subscription_tier="pro",
        subscription_period="monthly",
    )

    assert result["amount_sum"] == 300_000
    assert service.db_handler.insert_one.await_args.args[1]["amount_sum"] == 300_000
