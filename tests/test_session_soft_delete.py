"""Tests for session soft-delete.

Covers:
  * ``ChatHistoryService.delete_session`` — stamps ``archived`` on the session
    document and leaves message/file rows in place (they are retained on
    purpose; only the session flag hides the chat).
  * ``ChatHistoryService.get_share`` — a public share link stops resolving once
    its parent session is archived, and reports the same error as a share that
    never existed.
"""

from datetime import datetime
from unittest.mock import AsyncMock, MagicMock

import pytest

from core.config import settings
from core.exceptions import InvalidInputError


def _make_history_service():
    from services.chat_history_service import ChatHistoryService

    svc = ChatHistoryService.__new__(ChatHistoryService)
    svc.users_collection = settings.USERS_COLLECTION
    svc.sessions_collection = settings.SESSIONS_COLLECTION
    svc.messages_collection = settings.MESSAGES_COLLECTION
    svc.files_collection = settings.FILES_COLLECTION
    svc.db_manager = MagicMock()
    svc.db_manager.find_documents = AsyncMock(return_value=[])
    svc.db_manager.update_documents = AsyncMock(return_value=1)
    svc.db_manager.delete_documents = AsyncMock(return_value={"deleted_count": 0})
    return svc


# --------------------------------------------------------------------------- #
# delete_session                                                               #
# --------------------------------------------------------------------------- #
async def test_delete_session_stamps_archived_on_the_session():
    svc = _make_history_service()
    svc.db_manager.find_documents = AsyncMock(
        return_value=[{"_id": "ses-1", "user_id": "u1"}]
    )

    await svc.delete_session("ses-1")

    collection, query, update = svc.db_manager.update_documents.await_args.args
    assert collection == settings.SESSIONS_COLLECTION
    assert query == {"_id": "ses-1"}
    assert update["$set"]["archived"] is True
    assert isinstance(update["$set"]["archived_at"], datetime)


async def test_delete_session_retains_messages_and_files():
    """The whole point of the soft delete: message rows survive it."""
    svc = _make_history_service()
    svc.db_manager.find_documents = AsyncMock(
        return_value=[{"_id": "ses-1", "user_id": "u1"}]
    )

    await svc.delete_session("ses-1")

    svc.db_manager.delete_documents.assert_not_awaited()


async def test_delete_session_rejects_unknown_session():
    svc = _make_history_service()
    svc.db_manager.find_documents = AsyncMock(return_value=[])

    from core.exceptions import SessionNotFoundError

    with pytest.raises(SessionNotFoundError):
        await svc.delete_session("ses-missing")

    svc.db_manager.update_documents.assert_not_awaited()


# --------------------------------------------------------------------------- #
# get_share                                                                    #
# --------------------------------------------------------------------------- #
_SHARED_MESSAGE = {
    "_id": "msg-1",
    "session_id": "ses-1",
    "share_id": "sh-1",
    "content": {"query": "savol", "response": "javob"},
    "metadata": {"assistant": "court"},
}


async def test_get_share_resolves_while_the_session_is_active():
    svc = _make_history_service()
    svc.db_manager.find_documents = AsyncMock(
        side_effect=[[_SHARED_MESSAGE], [{"_id": "ses-1"}]]
    )

    share = await svc.get_share("sh-1")

    assert share.question == "savol"
    assert share.answer == "javob"


async def test_get_share_is_revoked_once_the_session_is_archived():
    """The message row stays active by design, so only the parent-session
    lookup can distinguish a live share from one whose chat was deleted."""
    svc = _make_history_service()
    # message found, parent session filtered out by _ACTIVE_ONLY
    svc.db_manager.find_documents = AsyncMock(side_effect=[[_SHARED_MESSAGE], []])

    with pytest.raises(InvalidInputError):
        await svc.get_share("sh-1")


async def test_get_share_checks_the_parent_session_with_the_active_filter():
    svc = _make_history_service()
    svc.db_manager.find_documents = AsyncMock(
        side_effect=[[_SHARED_MESSAGE], [{"_id": "ses-1"}]]
    )

    await svc.get_share("sh-1")

    collection, query = svc.db_manager.find_documents.await_args_list[1].args
    assert collection == settings.SESSIONS_COLLECTION
    assert query == {"_id": "ses-1", "archived": {"$ne": True}}
