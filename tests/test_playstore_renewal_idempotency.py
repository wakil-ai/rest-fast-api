"""Regression test for the GooglePlayService grant idempotency key.

Play's purchase_token stays the SAME across a subscription's renewals — only
latestOrderId changes each billing cycle. Keying the grant claim on
purchase_token (as this file originally did) would grant the first cycle and
then silently skip every renewal after it forever. This exercises the exact
sequence that bug would break: first purchase, then a renewal under the same
token but a new order id.
"""

from unittest.mock import AsyncMock

import pytest

from models.payment import GooglePlayError
from services.payments.playstore import GooglePlayService

_USER_ID = "user-1"
_PRODUCT_ID = "wakil.android.sub.standard.monthly"
_TOKEN = "opaque-play-purchase-token"


class _FakeCollection:
    """Minimal in-memory stand-in for the one Motor collection this file talks
    to directly (find_one_and_update / delete_one), keyed like the real
    unique index on order_id."""

    def __init__(self):
        self.docs: dict[str, dict] = {}

    async def find_one_and_update(self, filt, update, *, upsert, return_document):
        order_id = filt["order_id"]
        existing = self.docs.get(order_id)
        if existing is not None:
            return dict(existing)
        if upsert:
            self.docs[order_id] = dict(update["$setOnInsert"])
        return None

    async def delete_one(self, filt):
        order_id = filt.get("order_id")
        if order_id in self.docs and self.docs[order_id].get("granted") == filt.get("granted"):
            del self.docs[order_id]

    async def create_index(self, *args, **kwargs):
        return None

    async def drop_index(self, *args, **kwargs):
        return None


class _FakeDbHandler:
    """Stands in for MongoHandler's wrapper methods (find_one/update_one) plus
    the raw `.db[collection]` escape hatch this service uses for atomic claims."""

    def __init__(self):
        self._collection = _FakeCollection()
        self.db = {"playstore_transactions": self._collection}

    async def find_one(self, collection_name, query):
        for doc in self._collection.docs.values():
            if self._matches(doc, query):
                return doc
        return None

    async def update_one(self, collection_name, query, update):
        order_id = query.get("order_id")
        doc = self._collection.docs.get(order_id)
        if doc is None:
            return False
        doc.update(update)
        return True

    @staticmethod
    def _matches(doc, query) -> bool:
        for key, value in query.items():
            if isinstance(value, dict) and "$ne" in value:
                if doc.get(key) == value["$ne"]:
                    return False
            elif doc.get(key) != value:
                return False
        return True


def _make_service() -> GooglePlayService:
    service = GooglePlayService()
    service.db_handler = _FakeDbHandler()
    service.subscription_storage = AsyncMock()
    service.rate_limit_service = AsyncMock()
    return service


def _subscription_payload(*, order_id: str, expires_ms: int, state: str = "SUBSCRIPTION_STATE_ACTIVE") -> dict:
    # expiryTime as RFC3339 — _parse_rfc3339_ms converts this back to expires_ms.
    from datetime import datetime, timezone

    expiry_iso = datetime.fromtimestamp(expires_ms / 1000, tz=timezone.utc).isoformat().replace("+00:00", "Z")
    return {
        "subscriptionState": state,
        "latestOrderId": order_id,
        "lineItems": [{"productId": _PRODUCT_ID, "expiryTime": expiry_iso}],
        "acknowledgementState": "ACKNOWLEDGEMENT_STATE_ACKNOWLEDGED",
    }


@pytest.mark.asyncio
async def test_renewal_under_same_token_grants_again_with_new_order_id():
    service = _make_service()
    now_ms = 1_000_000_000_000
    day_ms = 24 * 60 * 60 * 1000

    first_purchase = _subscription_payload(order_id="GPA.0001", expires_ms=now_ms + 30 * day_ms)
    first_result = await service._grant_from_purchase(
        first_purchase,
        purchase_token=_TOKEN,
        product_id=_PRODUCT_ID,
        tier="standard",
        period="monthly",
        user_id=_USER_ID,
        now_ms=now_ms,
    )
    assert first_result["granted"] is True
    assert service.subscription_storage.upsert_subscription.await_count == 1

    # Same purchase_token (Play does not rotate it on a normal renewal), but a
    # new order id for the next billing cycle — this must ALSO grant.
    renewal_purchase = _subscription_payload(
        order_id="GPA.0001..1", expires_ms=now_ms + 60 * day_ms
    )
    renewal_result = await service._grant_from_purchase(
        renewal_purchase,
        purchase_token=_TOKEN,
        product_id=_PRODUCT_ID,
        tier="standard",
        period="monthly",
        user_id=_USER_ID,
        now_ms=now_ms + 31 * day_ms,
    )
    assert renewal_result["granted"] is True
    assert service.subscription_storage.upsert_subscription.await_count == 2


@pytest.mark.asyncio
async def test_replaying_the_same_order_id_does_not_double_grant():
    service = _make_service()
    now_ms = 1_000_000_000_000
    day_ms = 24 * 60 * 60 * 1000
    purchase = _subscription_payload(order_id="GPA.0002", expires_ms=now_ms + 30 * day_ms)

    first = await service._grant_from_purchase(
        purchase, purchase_token=_TOKEN, product_id=_PRODUCT_ID,
        tier="standard", period="monthly", user_id=_USER_ID, now_ms=now_ms,
    )
    second = await service._grant_from_purchase(
        purchase, purchase_token=_TOKEN, product_id=_PRODUCT_ID,
        tier="standard", period="monthly", user_id=_USER_ID, now_ms=now_ms + 1000,
    )

    assert first["granted"] is True
    assert second["granted"] is True
    # Only the first call should have actually written credits.
    assert service.subscription_storage.upsert_subscription.await_count == 1


@pytest.mark.asyncio
async def test_purchase_token_bound_to_a_different_user_is_rejected():
    service = _make_service()
    now_ms = 1_000_000_000_000
    day_ms = 24 * 60 * 60 * 1000
    purchase = _subscription_payload(order_id="GPA.0003", expires_ms=now_ms + 30 * day_ms)

    await service._grant_from_purchase(
        purchase, purchase_token=_TOKEN, product_id=_PRODUCT_ID,
        tier="standard", period="monthly", user_id=_USER_ID, now_ms=now_ms,
    )

    with pytest.raises(GooglePlayError):
        await service._grant_from_purchase(
            purchase, purchase_token=_TOKEN, product_id=_PRODUCT_ID,
            tier="standard", period="monthly", user_id="a-different-user", now_ms=now_ms,
        )
