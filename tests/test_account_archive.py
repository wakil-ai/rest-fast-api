"""Tests for account archive (soft-delete) — delete-my-account flow.

Covers:
  * ``AccountArchiveService.archive_user_account`` — idempotency, owned-collection
    coverage, str/int owner matching, payment/tax collections left untouched, and
    not-found / bad-input handling.
  * ``ChatHistoryService.is_user_archived`` — raw archived-flag probe used by the
    runtime guard.
  * ``ChatHistoryService`` read filtering — user-facing reads exclude archived data,
    while identity/raw lookups still see it.
"""

from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock

import pytest

from core.config import settings
from core.exceptions import InvalidInputError, UserNotFoundError


# --------------------------------------------------------------------------- #
# Fakes                                                                        #
# --------------------------------------------------------------------------- #
class _FakeCollection:
    def __init__(self):
        self.find_one = AsyncMock(return_value=None)
        self.update_many = AsyncMock(return_value=MagicMock(modified_count=1))
        self.update_one = AsyncMock(return_value=MagicMock(modified_count=1))


class _FakeDB:
    """dict-like Motor db: ``db[collection]`` returns a stable _FakeCollection."""

    def __init__(self):
        self.cols: dict[str, _FakeCollection] = {}

    def __getitem__(self, name: str) -> _FakeCollection:
        return self.cols.setdefault(name, _FakeCollection())


def _make_archive_service(user_doc):
    """Build an AccountArchiveService with a mocked Mongo db.

    ``user_doc`` is what the users collection's ``find_one`` returns (or None).
    """
    from services.account_archive_service import AccountArchiveService

    svc = AccountArchiveService.__new__(AccountArchiveService)
    svc.users_collection = settings.USERS_COLLECTION
    # Build the owned-collection map exactly as __init__ does, without touching the DB.
    svc._owned_collections = {
        settings.SESSIONS_COLLECTION: "user_id",
        settings.MESSAGES_COLLECTION: "user_id",
        settings.FILES_COLLECTION: "user_id",
        settings.PROJECTS_COLLECTION: "owner_id",
        settings.PROJECT_MEMBERS_COLLECTION: "user_id",
        settings.PROJECT_INVITES_COLLECTION: "created_by",
        settings.RATE_LIMIT_COLLECTION: "user_id",
        settings.SUBSCRIPTIONS_COLLECTION: "user_id",
        settings.DAILY_SUBSCRIPTIONS_COLLECTION: "user_id",
        settings.USER_PROMO_CODE_COLLECTION: "user_id",
        settings.TOKEN_COUNTING_COLLECTION: "user_id",
        settings.TELEGRAM_CHATS_COLLECTION: "user_id",
        settings.FINGERPRINTS_COLLECTION: "user_id",
    }

    db = _FakeDB()
    db[settings.USERS_COLLECTION].find_one = AsyncMock(return_value=user_doc)

    svc.db_manager = MagicMock()
    svc.db_manager.mongo_handler.db = db
    return svc, db


# --------------------------------------------------------------------------- #
# archive_user_account                                                         #
# --------------------------------------------------------------------------- #
async def test_archive_fresh_user_marks_all_owned_collections():
    svc, db = _make_archive_service({"_id": "u1"})

    result = await svc.archive_user_account("u1")

    assert result["already_archived"] is False
    assert isinstance(result["archived_at"], datetime)

    # Every owned collection got an idempotent update_many with the guard predicate.
    for coll_name, owner_field in svc._owned_collections.items():
        col = db[coll_name]
        col.update_many.assert_awaited_once()
        query, update = col.update_many.await_args.args
        assert query[owner_field] == {"$in": ["u1"]}
        assert query["archived"] == {"$ne": True}
        assert update["$set"]["archived"] is True

    # Users doc flipped via update_one, also guarded, and reported in the summary.
    users_col = db[settings.USERS_COLLECTION]
    users_col.update_one.assert_awaited_once()
    uq, _ = users_col.update_one.await_args.args
    assert uq == {"_id": "u1", "archived": {"$ne": True}}
    assert settings.USERS_COLLECTION in result["collections"]


async def test_archive_wipes_meta_ad_identifiers_from_the_user_doc():
    """The user doc survives the archive, so advertising IDs must be unset explicitly."""
    from services.meta_capi_service import AD_ATTRIBUTION_FIELDS

    svc, db = _make_archive_service({"_id": "u1"})

    await svc.archive_user_account("u1")

    _, update = db[settings.USERS_COLLECTION].update_one.await_args.args
    assert set(update["$unset"]) == set(AD_ATTRIBUTION_FIELDS)
    assert {"madid", "anon_id"} <= set(update["$unset"])


async def test_enterprise_rows_are_excluded_from_the_personal_sweep():
    """A member deleting their own account must not take the organization's work.

    Four collections hold enterprise rows keyed to the member who created them:
    `projects` (Cases), `sessions` (Case conversations), `messages` (their
    transcripts) and `files` (Case evidence). Unscoped, a personal deletion takes
    all four out of the organization, and every one of those losses is visible —
    `get_sessions_by_project`, `get_messages` and `get_file_by_id` all filter
    archived rows, so the Case keeps its session list while the transcripts come
    back empty and the files 404 on open.
    """
    from services.project_service import PERSONAL_SCOPE

    svc, db = _make_archive_service({"_id": "u1"})

    await svc.archive_user_account("u1")

    for coll_name in (
        settings.PROJECTS_COLLECTION,
        settings.SESSIONS_COLLECTION,
        settings.MESSAGES_COLLECTION,
        settings.FILES_COLLECTION,
    ):
        query, _ = db[coll_name].update_many.await_args.args
        assert query["$or"] == PERSONAL_SCOPE["$or"], (
            f"{coll_name} is swept without the personal scope"
        )

    # Collections with no enterprise half must NOT carry it — a stray org_id
    # predicate there would silently skip rows that should be archived.
    query, _ = db[settings.SUBSCRIPTIONS_COLLECTION].update_many.await_args.args
    assert "$or" not in query


async def test_archive_is_idempotent_for_already_archived_user():
    prior = datetime(2026, 1, 1, tzinfo=timezone.utc)
    svc, db = _make_archive_service(
        {"_id": "u1", "archived": True, "archived_at": prior}
    )

    result = await svc.archive_user_account("u1")

    # Reports the pre-existing state and the original timestamp, not "now".
    assert result["already_archived"] is True
    assert result["archived_at"] == prior
    # Still safe to run: the $ne:True predicate makes the writes no-ops.
    db[settings.SESSIONS_COLLECTION].update_many.assert_awaited_once()


async def test_archive_matches_int_owner_for_all_digit_ids():
    """telegram_chats stores user_id as int; digit ids must match str+int."""
    svc, db = _make_archive_service({"_id": "12345"})

    await svc.archive_user_account("12345")

    q, _ = db[settings.TELEGRAM_CHATS_COLLECTION].update_many.await_args.args
    assert q["user_id"] == {"$in": ["12345", 12345]}


async def test_archive_does_not_touch_payment_or_tax_collections():
    svc, db = _make_archive_service({"_id": "u1"})

    await svc.archive_user_account("u1")

    # Financial/tax collections follow a separate retention job — never written here.
    for coll in (
        settings.TRANSACTION_COLLECTION,
        settings.PAYME_INVOICES_COLLECTION,
        settings.CLICK_INVOICES_COLLECTION,
    ):
        assert coll not in db.cols, f"{coll} must not be archived"


async def test_archive_missing_user_raises_not_found():
    svc, _ = _make_archive_service(None)
    with pytest.raises(UserNotFoundError):
        await svc.archive_user_account("ghost")


async def test_archive_blank_user_id_raises_invalid_input():
    svc, _ = _make_archive_service({"_id": "u1"})
    with pytest.raises(InvalidInputError):
        await svc.archive_user_account("   ")


def test_owner_values_digit_and_non_digit():
    from services.account_archive_service import AccountArchiveService

    assert AccountArchiveService._owner_values("999") == ["999", 999]
    assert AccountArchiveService._owner_values("abc") == ["abc"]
    assert AccountArchiveService._owner_values(str(2**63 - 1)) == [
        str(2**63 - 1),
        2**63 - 1,
    ]
    assert AccountArchiveService._owner_values(str(2**63)) == [str(2**63)]
    assert AccountArchiveService._owner_values("9" * 100) == ["9" * 100]


# --------------------------------------------------------------------------- #
# is_user_archived + read filtering (ChatHistoryService)                       #
# --------------------------------------------------------------------------- #
def _make_history_service():
    from services.chat_history_service import ChatHistoryService

    svc = ChatHistoryService.__new__(ChatHistoryService)
    svc.users_collection = settings.USERS_COLLECTION
    svc.sessions_collection = settings.SESSIONS_COLLECTION
    svc.messages_collection = settings.MESSAGES_COLLECTION
    svc.files_collection = settings.FILES_COLLECTION
    svc.db_manager = MagicMock()
    svc.db_manager.find_documents = AsyncMock(return_value=[])
    return svc


async def test_is_user_archived_true_and_projection():
    svc = _make_history_service()
    db = _FakeDB()
    db[settings.USERS_COLLECTION].find_one = AsyncMock(
        return_value={"_id": "u1", "archived": True}
    )
    svc.db_manager.mongo_handler.db = db

    assert await svc.is_user_archived("u1") is True
    q, proj = db[settings.USERS_COLLECTION].find_one.await_args.args
    assert q == {"_id": "u1"}
    assert proj == {"archived": 1}


async def test_get_user_auth_status_uses_minimal_projection():
    svc = _make_history_service()
    db = _FakeDB()
    db[settings.USERS_COLLECTION].find_one = AsyncMock(
        return_value={"_id": "u1", "is_blocked": True, "archived": False}
    )
    svc.db_manager.mongo_handler.db = db

    assert await svc.get_user_auth_status("u1") == {
        "exists": True,
        "is_blocked": True,
        "archived": False,
    }
    query, projection = db[settings.USERS_COLLECTION].find_one.await_args.args
    assert query == {"_id": "u1"}
    assert projection == {"is_blocked": 1, "archived": 1}


async def test_is_user_archived_false_for_missing_or_active():
    svc = _make_history_service()
    db = _FakeDB()
    db[settings.USERS_COLLECTION].find_one = AsyncMock(return_value=None)
    svc.db_manager.mongo_handler.db = db
    assert await svc.is_user_archived("nobody") is False
    assert await svc.is_user_archived("") is False


async def test_get_session_excludes_archived():
    svc = _make_history_service()
    await svc.get_session("s1")
    coll, query = svc.db_manager.find_documents.await_args.args[:2]
    assert coll == settings.SESSIONS_COLLECTION
    assert query == {"_id": "s1", "archived": {"$ne": True}}


async def test_get_message_excludes_archived():
    svc = _make_history_service()
    await svc.get_message("m1")
    _, query = svc.db_manager.find_documents.await_args.args[:2]
    assert query == {"_id": "m1", "archived": {"$ne": True}}


async def test_get_messages_excludes_archived():
    svc = _make_history_service()
    await svc.get_messages("s1")
    _, query = svc.db_manager.find_documents.await_args.args[:2]
    assert query["session_id"] == "s1"
    assert query["archived"] == {"$ne": True}


async def test_get_file_by_id_excludes_archived():
    svc = _make_history_service()
    await svc.get_file_by_id("f1")
    _, query = svc.db_manager.find_documents.await_args.args[:2]
    assert query == {"_id": "f1", "archived": {"$ne": True}}
