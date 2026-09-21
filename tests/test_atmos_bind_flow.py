"""ATMOS bind lifecycle: start, resume own pending bind, 409 for others, cancel, status."""

import pytest
from pymongo.errors import DuplicateKeyError

from core.config import settings
from core.error_codes import ErrorCode
from models.payment import AtmosError
from services.payments.atmos import AtmosService
from tests.admin_fakes import FakeCollection, FakeMongoHandler, _matches


class LockCollection(FakeCollection):
    """Mongo semantics the lock relies on: upsert on a non-matching filter collides on _id."""

    async def find_one_and_update(self, query, update, upsert=False, **kwargs):
        for document in self.documents:
            if _matches(document, query):
                self._apply(document, update)
                return dict(document)
        if upsert:
            if any(doc.get("_id") == query.get("_id") for doc in self.documents):
                raise DuplicateKeyError("duplicate _id")
            document = {"_id": query.get("_id")}
            self._apply(document, update)
            self.documents.append(document)
        return None

    async def delete_one(self, query):
        self.documents = [doc for doc in self.documents if not _matches(doc, query)]


class Handler(FakeMongoHandler):
    async def find_one(self, name, query):
        return await self.db[name].find_one(query)

    async def insert_one(self, name, document):
        return await self.db[name].insert_one(document)

    async def update_one(self, name, query, update):
        result = await self.db[name].update_one(query, {"$set": update})
        return result.modified_count > 0


class FakeClient:
    def __init__(self):
        self.calls = 0

    async def create_card_bind(self, *, request_id, account, success_url):
        self.calls += 1
        return {"payment_id": self.calls, "url": f"https://dev-checkout.atmos.uz/bind?id={request_id}",
                "status": {"code": 0, "message": "Success"}}


def make_service() -> AtmosService:
    service = AtmosService.__new__(AtmosService)
    service.db_handler = Handler(atmos_bind_locks=LockCollection())
    service.client = FakeClient()
    service.mandates_collection = "atmos_mandates"
    service.bind_lock_collection = "atmos_bind_locks"
    service.transactions_collection = "atmos_transactions"
    service._atmos_indexes_ready = True

    async def _get_user(user_id):
        return {"_id": user_id}

    service.get_user_by_id = _get_user
    return service


async def test_same_user_click_again_resumes_instead_of_409() -> None:
    service = make_service()
    first = await service.start_bind(user_id="u1", success_url="https://x/r")
    second = await service.start_bind(user_id="u1", success_url="https://x/r")

    assert first["resumed"] is False
    assert second == {**first, "resumed": True}
    assert service.client.calls == 1


async def test_other_user_gets_coded_409_with_retry_after() -> None:
    service = make_service()
    await service.start_bind(user_id="u1", success_url="https://x/r")

    with pytest.raises(AtmosError) as exc:
        await service.start_bind(user_id="u2", success_url="https://x/r")

    assert exc.value.status_code == 409
    assert exc.value.code == ErrorCode.ATMOS_BIND_IN_PROGRESS
    assert 0 < exc.value.params["retry_after_seconds"] <= settings.ATMOS_BIND_LOCK_TTL_SECONDS


async def test_pending_bind_visible_then_cancel_frees_slot() -> None:
    service = make_service()
    await service.start_bind(user_id="u1", success_url="https://x/r")

    assert (await service.get_pending_bind("u1"))["status"] == "pending_bind"
    assert await service.get_pending_bind("u2") is None

    assert await service.cancel_bind("u1") is True
    assert await service.get_pending_bind("u1") is None
    assert await service.cancel_bind("u1") is False

    started = await service.start_bind(user_id="u2", success_url="https://x/r")
    assert started["resumed"] is False


async def test_expired_lock_is_not_resumable_and_old_pending_is_expired(monkeypatch) -> None:
    service = make_service()
    first = await service.start_bind(user_id="u1", success_url="https://x/r")
    lock = service.db_handler.db["atmos_bind_locks"].documents[0]
    lock["expires_at_ms"] = 0  # TTL elapsed

    assert await service.get_pending_bind("u1") is None
    second = await service.start_bind(user_id="u1", success_url="https://x/r")

    assert second["resumed"] is False
    assert second["request_id"] != first["request_id"]
    statuses = {d["request_id"]: d["status"] for d in service.db_handler.db["atmos_mandates"].documents}
    assert statuses == {first["request_id"]: "expired", second["request_id"]: "pending_bind"}
