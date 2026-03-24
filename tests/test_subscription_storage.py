from unittest.mock import patch

import pytest

from app.core.config import settings
from app.services.subscription_storage import SubscriptionStorage


class FakeUpdateResult:
    def __init__(self, modified_count: int):
        self.modified_count = modified_count


class FakeCollection:
    def __init__(self, documents=None):
        self.documents = [dict(document) for document in (documents or [])]
        self.indexes = []

    async def create_index(self, *args, **kwargs):
        self.indexes.append((args, kwargs))

    async def find_one(self, query):
        for document in self.documents:
            if all(document.get(key) == value for key, value in query.items()):
                return dict(document)
        return None

    async def update_one(self, query, update, upsert=False):
        for index, document in enumerate(self.documents):
            if all(document.get(key) == value for key, value in query.items()):
                updated_document = dict(document)
                updated_document.update(update.get("$set", {}))
                self.documents[index] = updated_document
                return FakeUpdateResult(1)

        if upsert:
            new_document = dict(query)
            new_document.update(update.get("$setOnInsert", {}))
            new_document.update(update.get("$set", {}))
            self.documents.append(new_document)
            return FakeUpdateResult(1)

        return FakeUpdateResult(0)


class FakeMongoHandler:
    def __init__(self, collections):
        self.db = collections


@pytest.mark.asyncio
async def test_upsert_subscription_extends_legacy_end_time():
    users = FakeCollection(
        [
            {
                "_id": "user-1",
                "user_id": "user-1",
                "subscription": {
                    "tier": "standard",
                    "period": "monthly",
                    "daily_credits": 200,
                    "start_ms": 1000,
                    "end_ms": 5000,
                },
            }
        ]
    )
    subscriptions = FakeCollection()
    daily_subscriptions = FakeCollection()
    fake_handler = FakeMongoHandler(
        {
            settings.USERS_COLLECTION: users,
            settings.SUBSCRIPTIONS_COLLECTION: subscriptions,
            settings.DAILY_SUBSCRIPTIONS_COLLECTION: daily_subscriptions,
        }
    )

    with patch(
        "app.services.subscription_storage.get_mongo_handler",
        return_value=fake_handler,
    ):
        storage = SubscriptionStorage()
        result = await storage.upsert_subscription(
            user_id="user-1",
            quote={
                "tier": "pro",
                "period": "monthly",
                "daily_credits": 400,
                "days": 30,
                "total_credits": 12000,
                "amount_sum": 600000,
            },
            order_id="order-1",
            transaction_id="tx-1",
            now_ms=2000,
            provider="payme",
        )

    assert result["start_ms"] == 5000
    assert result["end_ms"] == 5000 + 30 * 24 * 60 * 60 * 1000
    assert subscriptions.documents[0]["last_order_id"] == "order-1"
    assert subscriptions.documents[0]["provider"] == "payme"


@pytest.mark.asyncio
async def test_get_daily_subscription_prefers_new_collection_over_legacy_user_field():
    users = FakeCollection(
        [
            {
                "_id": "user-2",
                "user_id": "user-2",
                "daily_pass": {
                    "daily_credits": 100,
                    "start_ms": 100,
                    "end_ms": 200,
                },
            }
        ]
    )
    subscriptions = FakeCollection()
    daily_subscriptions = FakeCollection(
        [
            {
                "user_id": "user-2",
                "tier": "daily",
                "period": "daily",
                "daily_credits": 300,
                "start_ms": 1000,
                "end_ms": 2000,
            }
        ]
    )
    fake_handler = FakeMongoHandler(
        {
            settings.USERS_COLLECTION: users,
            settings.SUBSCRIPTIONS_COLLECTION: subscriptions,
            settings.DAILY_SUBSCRIPTIONS_COLLECTION: daily_subscriptions,
        }
    )

    with patch(
        "app.services.subscription_storage.get_mongo_handler",
        return_value=fake_handler,
    ):
        storage = SubscriptionStorage()
        daily_subscription = await storage.get_daily_subscription("user-2")

    assert daily_subscription["daily_credits"] == 300
    assert daily_subscription["start_ms"] == 1000
    assert daily_subscription["end_ms"] == 2000
