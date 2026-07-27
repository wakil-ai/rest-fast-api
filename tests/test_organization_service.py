"""Tests for organization creation and the "my organizations" listing.

Services are built with ``__new__`` so ``__init__``'s lazy index task never runs;
the Mongo layer is a fake that records writes.
"""

from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import HTTPException

from core.config import settings


class _FakeDBManager:
    """Records inserts and answers finds from a per-collection list."""

    def __init__(self):
        self.docs: dict[str, list[dict]] = {}

    async def create_collection(self, name):
        self.docs.setdefault(name, [])

    async def insert_documents(self, name, documents):
        self.docs.setdefault(name, []).extend(documents)
        return [d["_id"] for d in documents]

    async def find_documents(self, name, query, limit=50, skip=0):
        rows = self.docs.get(name, [])
        out = [r for r in rows if _matches(r, query)]
        return out[skip : skip + limit]

    async def update_documents(self, name, query, update, upsert=False):
        rows = [r for r in self.docs.get(name, []) if _matches(r, query)]
        for r in rows:
            r.update(update.get("$set", {}))
        return len(rows)


def _matches(row: dict, query: dict) -> bool:
    for key, cond in query.items():
        val = row.get(key)
        if isinstance(cond, dict):
            if "$ne" in cond and val == cond["$ne"]:
                return False
            if "$in" in cond and val not in cond["$in"]:
                return False
        elif val != cond:
            return False
    return True


def _make_org_service():
    from services.organization_service import OrganizationService

    svc = OrganizationService.__new__(OrganizationService)
    svc.db = _FakeDBManager()
    svc.history = MagicMock()
    svc.history.get_user = AsyncMock(return_value={"_id": "u1", "first_name": "Ada"})
    svc.history._ensure_user_exists = AsyncMock(return_value=None)
    svc.orgs_collection = settings.ORGANIZATIONS_COLLECTION
    svc.members_collection = settings.ORGANIZATION_MEMBERS_COLLECTION
    svc.org_prefix = "org-"
    svc.member_prefix = "omem-"
    return svc


async def test_create_organization_writes_org_and_admin_membership():
    svc = _make_org_service()

    org = await svc.create_organization(user_id="u1", name="Legal Dept")

    assert org["_id"].startswith("org-")
    assert org["name"] == "Legal Dept"
    assert org["created_by"] == "u1"
    assert org["status"] == "active"
    assert org["seat_limit"] == settings.ORG_SEAT_LIMIT
    assert org["archived"] is False
    assert isinstance(org["created_at"], datetime)
    assert org["role"] == "admin"

    members = svc.db.docs[settings.ORGANIZATION_MEMBERS_COLLECTION]
    assert len(members) == 1
    assert members[0]["_id"].startswith("omem-")
    assert members[0]["org_id"] == org["_id"]
    assert members[0]["user_id"] == "u1"
    assert members[0]["role"] == "admin"
    assert members[0]["status"] == "active"


async def test_create_organization_rejects_blank_name():
    svc = _make_org_service()

    with pytest.raises(HTTPException) as exc:
        await svc.create_organization(user_id="u1", name="   ")

    assert exc.value.status_code == 400


async def test_create_organization_rejects_unknown_user():
    from core.exceptions import UserNotFoundError

    svc = _make_org_service()
    svc.history._ensure_user_exists = AsyncMock(side_effect=UserNotFoundError("ghost"))

    with pytest.raises(UserNotFoundError):
        await svc.create_organization(user_id="ghost", name="Legal Dept")

    assert svc.db.docs.get(settings.ORGANIZATIONS_COLLECTION, []) == []


async def test_assert_org_admin_allows_admin_and_rejects_member():
    svc = _make_org_service()
    org = await svc.create_organization(user_id="u1", name="Legal Dept")
    svc.db.docs[settings.ORGANIZATION_MEMBERS_COLLECTION].append(
        {
            "_id": "omem-2",
            "org_id": org["_id"],
            "user_id": "u2",
            "role": "member",
            "status": "active",
            "joined_at": datetime.now(timezone.utc),
        }
    )

    assert (await svc.assert_org_admin(org["_id"], "u1"))["_id"] == org["_id"]

    with pytest.raises(HTTPException) as exc:
        await svc.assert_org_admin(org["_id"], "u2")
    assert exc.value.status_code == 403


async def test_list_user_organizations_returns_orgs_with_caller_role():
    svc = _make_org_service()
    org_a = await svc.create_organization(user_id="u1", name="Org A")
    org_b = await svc.create_organization(user_id="u2", name="Org B")
    # u1 is also a plain member of Org B.
    svc.db.docs[settings.ORGANIZATION_MEMBERS_COLLECTION].append(
        {
            "_id": "omem-x",
            "org_id": org_b["_id"],
            "user_id": "u1",
            "role": "member",
            "status": "active",
            "joined_at": datetime.now(timezone.utc),
        }
    )

    out = await svc.list_user_organizations("u1")

    by_id = {o["_id"]: o for o in out}
    assert set(by_id) == {org_a["_id"], org_b["_id"]}
    assert by_id[org_a["_id"]]["role"] == "admin"
    assert by_id[org_b["_id"]]["role"] == "member"
    assert by_id[org_b["_id"]]["member_count"] == 2


async def test_list_user_organizations_skips_removed_membership():
    svc = _make_org_service()
    org = await svc.create_organization(user_id="u1", name="Org A")
    svc.db.docs[settings.ORGANIZATION_MEMBERS_COLLECTION].append(
        {
            "_id": "omem-gone",
            "org_id": org["_id"],
            "user_id": "u9",
            "role": "member",
            "status": "removed",
            "joined_at": datetime.now(timezone.utc),
        }
    )

    assert await svc.list_user_organizations("u9") == []


async def test_list_user_organizations_empty_for_unknown_user():
    svc = _make_org_service()

    assert await svc.list_user_organizations("nobody") == []


async def test_active_member_count_ignores_removed_rows():
    svc = _make_org_service()
    org = await svc.create_organization(user_id="u1", name="Legal Dept")
    svc.db.docs[settings.ORGANIZATION_MEMBERS_COLLECTION].append(
        {
            "_id": "omem-2",
            "org_id": org["_id"],
            "user_id": "u2",
            "role": "member",
            "status": "removed",
            "joined_at": datetime.now(timezone.utc),
        }
    )

    assert await svc.active_member_count(org["_id"]) == 1
