"""Org-shared sessions: one transcript per Case, readable by every member.

The tests that matter most are the negative ones — a non-shared session must
behave exactly as it does today, and sharing must not hand out delete rights.
"""

from datetime import datetime, timezone
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import HTTPException

from core.exceptions import InvalidInputError
from services.chat_history_service import ChatHistoryService


def session_doc(**overrides: Any) -> dict[str, Any]:
    doc: dict[str, Any] = {
        "_id": "ses-1",
        "session_id": "ses-1",
        "user_id": "creator",
        "org_id": "org-1",
        "project_id": "proj-1",
        "task_id": None,
        "shared": False,
        "status": "active",
        "created_at": datetime.now(timezone.utc),
    }
    doc.update(overrides)
    return doc


@pytest.fixture
def service() -> ChatHistoryService:
    svc = ChatHistoryService.__new__(ChatHistoryService)
    svc._ensure_session_exists = AsyncMock()  # type: ignore[method-assign]
    return svc


@pytest.fixture
def org_member_ok(monkeypatch: pytest.MonkeyPatch) -> MagicMock:
    orgs = MagicMock()
    orgs.assert_org_member = AsyncMock(return_value={"_id": "org-1"})
    monkeypatch.setattr(
        "services.chat_history_service.get_organization_service",
        lambda: orgs,
    )
    return orgs


async def test_shared_session_readable_by_other_member(service: Any, org_member_ok: MagicMock) -> None:
    service._ensure_session_exists.return_value = session_doc(shared=True)

    result = await service.assert_session_access("ses-1", "member-b")

    assert result["_id"] == "ses-1"
    org_member_ok.assert_org_member.assert_awaited_once_with("org-1", "member-b")


async def test_non_shared_org_session_still_rejects_non_owner(service: Any, org_member_ok: MagicMock) -> None:
    service._ensure_session_exists.return_value = session_doc(shared=False)

    with pytest.raises(HTTPException) as exc:
        await service.assert_session_access("ses-1", "member-b")

    assert exc.value.status_code == 403


async def test_shared_flag_without_org_id_falls_through_to_ownership(
    service: Any, org_member_ok: MagicMock
) -> None:
    """Both halves are required. A personal session that somehow acquired the
    flag must not become world-readable."""
    service._ensure_session_exists.return_value = session_doc(shared=True, org_id=None)

    with pytest.raises(HTTPException) as exc:
        await service.assert_session_access("ses-1", "member-b")

    assert exc.value.status_code == 403


async def test_assert_session_owner_rejects_non_creator_of_shared_session(
    service: Any, org_member_ok: MagicMock
) -> None:
    """Sharing a transcript must not hand every member the ability to delete it."""
    service._ensure_session_exists.return_value = session_doc(shared=True)

    with pytest.raises(HTTPException) as exc:
        await service.assert_session_owner("ses-1", "member-b")

    assert exc.value.status_code == 403


async def test_assert_session_owner_allows_creator(service: Any, org_member_ok: MagicMock) -> None:
    service._ensure_session_exists.return_value = session_doc(shared=True)

    result = await service.assert_session_owner("ses-1", "creator")

    assert result["_id"] == "ses-1"


async def test_ensure_session_for_user_allows_member_on_shared(service: Any, org_member_ok: MagicMock) -> None:
    service._ensure_session_exists.return_value = session_doc(shared=True)

    result = await service.ensure_session_for_user("member-b", "ses-1")

    assert result["_id"] == "ses-1"


async def test_ensure_session_for_user_rejects_non_owner_on_personal(
    service: Any, org_member_ok: MagicMock
) -> None:
    service._ensure_session_exists.return_value = session_doc(shared=False, org_id=None)

    with pytest.raises(InvalidInputError):
        await service.ensure_session_for_user("member-b", "ses-1")


async def test_ensure_shared_session_returns_existing(org_member_ok: MagicMock) -> None:
    svc = ChatHistoryService.__new__(ChatHistoryService)
    existing = session_doc(shared=True)
    collection = MagicMock()
    collection.find_one = AsyncMock(return_value=existing)
    svc._sessions_collection = lambda: collection  # type: ignore[method-assign]
    svc.create_session = AsyncMock()  # type: ignore[method-assign]

    result = await svc.ensure_shared_session(
        user_id="member-b", org_id="org-1", project_id="proj-1"
    )

    assert result is existing
    svc.create_session.assert_not_awaited()
    # The older duplicate must win permanently, so the lookup is sorted.
    assert collection.find_one.await_args.kwargs["sort"] == [
        ("created_at", 1),
        ("_id", 1),
    ]


async def test_ensure_shared_session_creates_when_absent(org_member_ok: MagicMock) -> None:
    svc = ChatHistoryService.__new__(ChatHistoryService)
    collection = MagicMock()
    collection.find_one = AsyncMock(return_value=None)
    svc._sessions_collection = lambda: collection  # type: ignore[method-assign]
    created = session_doc(_id="ses-new", shared=True)
    svc.create_session = AsyncMock(return_value=created)  # type: ignore[method-assign]

    result = await svc.ensure_shared_session(
        user_id="creator", org_id="org-1", project_id="proj-1", task_id="task-1"
    )

    assert result is created
    call = svc.create_session.await_args
    assert call is not None
    assert call.kwargs["shared"] is True
    assert call.kwargs["task_id"] == "task-1"


async def test_a_racing_duplicate_converges_on_the_older_session(org_member_ok: MagicMock) -> None:
    """Both racers miss and both insert. Sorting the *lookup* is not enough — the
    racer that inserted the newer row would keep and generate into it, while every
    later read resolves to the older one, stranding a paid-for transcript where
    nothing can reach it. The re-read after insert is what makes them converge."""
    svc = ChatHistoryService.__new__(ChatHistoryService)
    older = session_doc(_id="ses-older", shared=True)
    collection = MagicMock()
    # Miss, then the loser's own insert plus the winner's row are both visible.
    collection.find_one = AsyncMock(side_effect=[None, older])
    svc._sessions_collection = lambda: collection  # type: ignore[method-assign]
    mine = session_doc(_id="ses-newer", shared=True)
    svc.create_session = AsyncMock(return_value=mine)  # type: ignore[method-assign]

    result = await svc.ensure_shared_session(
        user_id="creator", org_id="org-1", project_id="proj-1"
    )

    assert result["_id"] == "ses-older"
    assert collection.find_one.await_args.kwargs["sort"] == [
        ("created_at", 1),
        ("_id", 1),
    ]


async def test_ensure_shared_session_checks_membership_on_reuse(org_member_ok: MagicMock) -> None:
    """Reuse is not a free pass: a departed member must not inherit the transcript."""
    svc = ChatHistoryService.__new__(ChatHistoryService)
    collection = MagicMock()
    collection.find_one = AsyncMock(return_value=session_doc(shared=True))
    svc._sessions_collection = lambda: collection  # type: ignore[method-assign]
    svc.create_session = AsyncMock()  # type: ignore[method-assign]

    await svc.ensure_shared_session(
        user_id="member-b", org_id="org-1", project_id="proj-1"
    )

    org_member_ok.assert_org_member.assert_awaited_once_with("org-1", "member-b")
