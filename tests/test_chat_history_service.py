from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.core.config import settings
from app.core.exceptions import InvalidInputError
from app.models.chat_history import SessionStatus
from app.services.chat_history_service import ChatHistoryService
from scripts.cleanup_empty_sessions import _build_empty_sessions_pipeline


class _AsyncCursor:
    def __init__(self, documents):
        self.documents = documents

    def __aiter__(self):
        async def _iterator():
            for document in self.documents:
                yield document

        return _iterator()


class _FakeCollection:
    def __init__(self, aggregate_documents=None):
        self.aggregate_documents = aggregate_documents or []
        self.aggregate_pipeline = None

    def aggregate(self, pipeline):
        self.aggregate_pipeline = pipeline
        return _AsyncCursor(self.aggregate_documents)


class _FakeDBManager:
    def __init__(self, collections):
        self.collections = collections
        self.mongo_handler = SimpleNamespace(db=collections)
        self.insert_calls = []
        self.update_calls = []

    async def create_collection(self, collection_name):  # noqa: ARG002
        return None

    async def insert_documents(self, collection_name, documents):
        self.insert_calls.append((collection_name, documents))
        return [document.get("_id") for document in documents]

    async def update_documents(self, collection_name, query, update, upsert=False):  # noqa: ARG002
        self.update_calls.append((collection_name, query, update, upsert))
        return 1

    async def find_documents(self, collection_name, query, limit=50):  # noqa: ARG002
        if collection_name == settings.USERS_COLLECTION:
            return [{"_id": query.get("_id"), "user_id": query.get("_id")}]
        return []


def _build_service(monkeypatch, *, session_documents=None):
    collections = {
        settings.SESSIONS_COLLECTION: _FakeCollection(session_documents),
        settings.MESSAGES_COLLECTION: _FakeCollection(),
        settings.FILES_COLLECTION: _FakeCollection(),
        settings.USERS_COLLECTION: _FakeCollection(),
        settings.TOKEN_COUNTING_COLLECTION: _FakeCollection(),
    }
    db_manager = _FakeDBManager(collections)

    def _fake_create_task(coro):
        coro.close()
        return None

    import app.services.chat_history_service as chat_history_service_module

    monkeypatch.setattr(chat_history_service_module, "get_db_manager", lambda: db_manager)
    monkeypatch.setattr(chat_history_service_module.asyncio, "create_task", _fake_create_task)

    return ChatHistoryService(), db_manager, collections


@pytest.mark.asyncio
async def test_create_session_starts_as_draft(monkeypatch):
    service, db_manager, _ = _build_service(monkeypatch)
    service._ensure_user_exists = AsyncMock(return_value={"_id": "u1"})

    session = await service.create_session(user_id="u1", title="Hello")

    assert session["status"] == SessionStatus.draft.value
    assert session["activated_at"] is None
    assert db_manager.insert_calls[0][0] == settings.SESSIONS_COLLECTION
    assert db_manager.insert_calls[0][1][0]["status"] == SessionStatus.draft.value


@pytest.mark.asyncio
async def test_add_message_activates_draft_session(monkeypatch):
    service, db_manager, _ = _build_service(monkeypatch)
    service._ensure_session_exists = AsyncMock(
        return_value={
            "_id": "ses-1",
            "user_id": "u1",
            "status": SessionStatus.draft.value,
            "activated_at": None,
        }
    )

    message = await service.add_message(
        session_id="ses-1",
        file_ids=None,
        content={"query": "Hi", "response": "Hello"},
    )

    assert message["session_id"] == "ses-1"
    assert db_manager.update_calls[0][0] == settings.SESSIONS_COLLECTION
    update_payload = db_manager.update_calls[0][2]["$set"]
    assert update_payload["status"] == SessionStatus.active.value
    assert update_payload["activated_at"] is not None


@pytest.mark.asyncio
async def test_add_message_rejects_file_not_owned_by_session_user(monkeypatch):
    service, db_manager, _ = _build_service(monkeypatch)
    service._ensure_session_exists = AsyncMock(
        return_value={
            "_id": "ses-1",
            "user_id": "u1",
            "status": SessionStatus.draft.value,
            "activated_at": None,
        }
    )

    async def find_documents(collection_name, query, limit=50):  # noqa: ARG001
        if collection_name == settings.FILES_COLLECTION:
            return [{"_id": query.get("_id"), "user_id": "other-user"}]
        return []

    monkeypatch.setattr(db_manager, "find_documents", find_documents)

    with pytest.raises(InvalidInputError, match="does not belong to this user"):
        await service.add_message(
            session_id="ses-1",
            file_ids=["f1"],
            content={"query": "Hi", "response": "Hello"},
        )


@pytest.mark.asyncio
async def test_get_sessions_uses_active_visibility_pipeline(monkeypatch):
    visible_sessions = [{"_id": "ses-active", "status": SessionStatus.active.value}]
    service, _, collections = _build_service(
        monkeypatch,
        session_documents=visible_sessions,
    )
    service._ensure_user_exists = AsyncMock(return_value={"_id": "u1"})

    sessions = await service.get_sessions(user_id="u1", limit=10)

    assert sessions == visible_sessions
    pipeline = collections[settings.SESSIONS_COLLECTION].aggregate_pipeline
    assert pipeline[0] == {"$match": {"user_id": "u1"}}
    assert pipeline[1]["$lookup"]["from"] == settings.MESSAGES_COLLECTION
    visibility_match = pipeline[2]["$match"]["$or"]
    assert {"status": SessionStatus.active.value} in visibility_match
    assert pipeline[3] == {"$sort": {"updated_at": -1}}
    assert pipeline[4] == {"$limit": 10}


def test_cleanup_pipeline_targets_stale_draft_sessions_only():
    cutoff = __import__("datetime").datetime(2026, 1, 1)

    pipeline = _build_empty_sessions_pipeline(
        messages_collection_name=settings.MESSAGES_COLLECTION,
        user_id="u1",
        older_than=cutoff,
        limit=25,
    )

    assert pipeline[0] == {
        "$match": {
            "status": SessionStatus.draft.value,
            "created_at": {"$lt": cutoff},
            "user_id": "u1",
        }
    }
    assert pipeline[-1] == {"$limit": 25}


@pytest.mark.asyncio
async def test_get_recent_messages_returns_last_n_in_chronological_order(monkeypatch):
    service, _, _ = _build_service(monkeypatch)
    older = {"_id": "m1", "created_at": __import__("datetime").datetime(2026, 1, 1)}
    middle = {"_id": "m2", "created_at": __import__("datetime").datetime(2026, 1, 2)}
    latest = {"_id": "m3", "created_at": __import__("datetime").datetime(2026, 1, 3)}
    service.get_messages = AsyncMock(return_value=[latest, older, middle])

    messages = await service.get_recent_messages("ses-1", limit=2)

    assert [message["_id"] for message in messages] == ["m2", "m3"]
