from unittest.mock import patch

import pytest

from app.core.config import settings
from app.services.payments.base import BasePaymentService


class FakeCollection:
    def __init__(self, documents=None):
        self.documents = [dict(document) for document in (documents or [])]
        self.indexes = []

    async def create_index(self, *args, **kwargs):
        self.indexes.append((args, kwargs))

    async def find_one(self, query):
        for document in self.documents:
            if _matches(document, query):
                return dict(document)
        return None

    async def insert_one(self, document):
        self.documents.append(dict(document))


def _matches(document: dict, query: dict) -> bool:
    for key, value in query.items():
        if key == "$or":
            if not any(_matches(document, branch) for branch in value):
                return False
            continue

        current = document
        for part in key.split("."):
            if not isinstance(current, dict) or part not in current:
                return False
            current = current[part]

        if current != value:
            return False

    return True


class FakeMongoHandler:
    def __init__(self, collections):
        self.db = collections

    async def find_one(self, collection_name, query):
        return await self.db[collection_name].find_one(query)

    async def insert_one(self, collection_name, document):
        await self.db[collection_name].insert_one(document)
        return "fake-id"


class FakePaymentService(BasePaymentService):
    def __init__(self, provider: str):
        self.provider = provider
        super().__init__()

    async def build_payment_link(
        self, *, amount_sum: int, user_id: str, callback_url: str, order_id: str
    ) -> str:
        return f"{self.provider}:{order_id}:{amount_sum}:{user_id}"


@pytest.mark.asyncio
async def test_init_payment_writes_to_shared_invoice_collection_with_provider():
    users = FakeCollection([{"_id": "user-1", "user_id": "user-1"}])
    invoices = FakeCollection()
    subscriptions = FakeCollection()
    daily_subscriptions = FakeCollection()
    fake_handler = FakeMongoHandler(
        {
            settings.USERS_COLLECTION: users,
            settings.PAYMENT_INVOICES_COLLECTION: invoices,
            settings.SUBSCRIPTIONS_COLLECTION: subscriptions,
            settings.DAILY_SUBSCRIPTIONS_COLLECTION: daily_subscriptions,
        }
    )

    with patch(
        "app.services.payments.base.get_mongo_handler",
        return_value=fake_handler,
    ), patch(
        "app.services.subscription_storage.get_mongo_handler",
        return_value=fake_handler,
    ):
        service = FakePaymentService("payme")
        result = await service.init_payment(
            amount_sum=15000,
            user_id="user-1",
            callback_url="https://example.com/callback",
            order_id="shared-order",
        )

    assert result["order_id"] == "shared-order"
    assert invoices.documents[0]["provider"] == "payme"
    assert invoices.documents[0]["order_id"] == "shared-order"


@pytest.mark.asyncio
async def test_same_order_id_can_exist_per_provider_in_shared_collection():
    users = FakeCollection([{"_id": "user-1", "user_id": "user-1"}])
    invoices = FakeCollection()
    subscriptions = FakeCollection()
    daily_subscriptions = FakeCollection()
    fake_handler = FakeMongoHandler(
        {
            settings.USERS_COLLECTION: users,
            settings.PAYMENT_INVOICES_COLLECTION: invoices,
            settings.SUBSCRIPTIONS_COLLECTION: subscriptions,
            settings.DAILY_SUBSCRIPTIONS_COLLECTION: daily_subscriptions,
        }
    )

    with patch(
        "app.services.payments.base.get_mongo_handler",
        return_value=fake_handler,
    ), patch(
        "app.services.subscription_storage.get_mongo_handler",
        return_value=fake_handler,
    ):
        payme_service = FakePaymentService("payme")
        click_service = FakePaymentService("click")

        await payme_service.init_payment(
            amount_sum=15000,
            user_id="user-1",
            callback_url="https://example.com/payme",
            order_id="same-order",
        )
        await click_service.init_payment(
            amount_sum=15000,
            user_id="user-1",
            callback_url="https://example.com/click",
            order_id="same-order",
        )

    assert len(invoices.documents) == 2
    assert {invoice["provider"] for invoice in invoices.documents} == {"payme", "click"}
