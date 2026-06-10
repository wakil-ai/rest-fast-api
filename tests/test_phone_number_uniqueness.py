"""Tests for phone-number uniqueness enforcement in ChatHistoryService.

Covers the application-level check (`_assert_phone_number_available`), the
`update_user_phone_number` write path, and the `create_user` path, including the
unique-index `DuplicateKeyError`/`E11000` race backstops.
"""

from unittest.mock import AsyncMock, MagicMock

import pytest
from pymongo.errors import DuplicateKeyError

from core.exceptions import PhoneNumberAlreadyExistsError
from services.chat_history_service import ChatHistoryService

PHONE = "+998901234567"
USER_ID = "user-1"


def _make_service() -> ChatHistoryService:
    """Build a ChatHistoryService with a fully mocked db_manager."""
    service = ChatHistoryService.__new__(ChatHistoryService)
    service.users_collection = "users"

    db_manager = MagicMock()
    db_manager.find_documents = AsyncMock(return_value=[])
    db_manager.update_documents = AsyncMock(return_value=1)
    db_manager.insert_documents = AsyncMock(return_value=[USER_ID])
    db_manager.mongo_handler = MagicMock()
    db_manager.mongo_handler.find_one = AsyncMock(return_value=None)
    service.db_manager = db_manager

    # Bitrix lead creation is a side effect we don't exercise here.
    service._create_bitrix_lead_if_needed = AsyncMock(side_effect=lambda user: user)
    return service


# --- _assert_phone_number_available ----------------------------------------


@pytest.mark.asyncio
async def test_assert_passes_when_number_free():
    service = _make_service()
    service.db_manager.mongo_handler.find_one.return_value = None

    await service._assert_phone_number_available(PHONE)  # should not raise


@pytest.mark.asyncio
async def test_assert_raises_409_when_number_taken():
    service = _make_service()
    service.db_manager.mongo_handler.find_one.return_value = {"_id": "someone-else"}

    with pytest.raises(PhoneNumberAlreadyExistsError) as exc:
        await service._assert_phone_number_available(PHONE)
    assert exc.value.status_code == 409


@pytest.mark.asyncio
async def test_assert_excludes_self_from_query():
    service = _make_service()

    await service._assert_phone_number_available(PHONE, exclude_user_id=USER_ID)

    _, query = service.db_manager.mongo_handler.find_one.call_args.args
    assert query["phone_number"] == PHONE
    assert query["_id"] == {"$ne": USER_ID}


# --- update_user_phone_number ----------------------------------------------


@pytest.mark.asyncio
async def test_update_rejects_number_owned_by_another_user():
    service = _make_service()
    service.db_manager.find_documents.return_value = [{"_id": USER_ID}]  # user exists
    service.db_manager.mongo_handler.find_one.return_value = {"_id": "other-user"}

    with pytest.raises(PhoneNumberAlreadyExistsError):
        await service.update_user_phone_number(USER_ID, PHONE)

    service.db_manager.update_documents.assert_not_awaited()


@pytest.mark.asyncio
async def test_update_allows_resubmitting_own_number():
    service = _make_service()
    service.db_manager.find_documents.return_value = [
        {"_id": USER_ID, "phone_number": PHONE}
    ]
    # Excluded by _id, so no conflicting doc is found.
    service.db_manager.mongo_handler.find_one.return_value = None

    user = await service.update_user_phone_number(USER_ID, PHONE)

    assert user is not None
    service.db_manager.update_documents.assert_awaited_once()


@pytest.mark.asyncio
async def test_update_maps_duplicate_key_error_to_409():
    service = _make_service()
    service.db_manager.find_documents.return_value = [{"_id": USER_ID}]
    service.db_manager.mongo_handler.find_one.return_value = None  # passes app check
    service.db_manager.update_documents.side_effect = DuplicateKeyError(
        "E11000 duplicate key error ... index: uniq_phone_number"
    )

    with pytest.raises(PhoneNumberAlreadyExistsError) as exc:
        await service.update_user_phone_number(USER_ID, PHONE)
    assert exc.value.status_code == 409


# --- create_user ------------------------------------------------------------


@pytest.mark.asyncio
async def test_create_user_rejects_duplicate_phone():
    service = _make_service()
    service.db_manager.find_documents.return_value = []  # no existing _id
    service.db_manager.mongo_handler.find_one.return_value = {"_id": "other-user"}

    with pytest.raises(PhoneNumberAlreadyExistsError):
        await service.create_user(user_id="new-user", phone_number=PHONE)

    service.db_manager.insert_documents.assert_not_awaited()


@pytest.mark.asyncio
async def test_create_user_maps_phone_index_e11000_to_409():
    service = _make_service()
    service.db_manager.find_documents.return_value = []
    service.db_manager.mongo_handler.find_one.return_value = None  # passes app check
    service.db_manager.insert_documents.side_effect = DuplicateKeyError(
        "E11000 duplicate key error collection: wakilai.users index: uniq_phone_number"
    )

    with pytest.raises(PhoneNumberAlreadyExistsError) as exc:
        await service.create_user(user_id="new-user", phone_number=PHONE)
    assert exc.value.status_code == 409


@pytest.mark.asyncio
async def test_create_user_id_collision_returns_existing_user():
    service = _make_service()
    existing = {"_id": "new-user", "phone_number": None}
    # First call: existing-by-_id check (empty). Second: post-collision lookup.
    service.db_manager.find_documents.side_effect = [[], [existing]]
    service.db_manager.insert_documents.side_effect = DuplicateKeyError(
        "E11000 duplicate key error collection: wakilai.users index: _id_"
    )

    result = await service.create_user(user_id="new-user")

    assert result == existing
