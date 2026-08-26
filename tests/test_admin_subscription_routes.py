"""Route contract for the admin subscription surface: auth, envelope, idempotency."""

import time
import uuid

import pytest
from fastapi.testclient import TestClient

from api.v2 import admin_subscriptions
from core.config import settings
from core.dependencies import (
    get_admin_audit_service,
    get_admin_subscription_service,
)
from models.admin_subscription import AdminGrantRequest
from services.admin_audit_service import AdminActionAlreadyRecorded
from services.admin_subscription_service import AdminSubscriptionService
from services.rate_limit_service import RateLimitService
from services.subscription_storage import SubscriptionStorage
from tests.admin_fakes import FakeCollection, FakeMongoHandler, FakeTransactionService
from tests.conftest import make_admin_app

USER_ID = "7470310475"
DAY_MS = 86_400_000

CATALOG = {
    ("standard", "monthly"): {
        "tier": "standard",
        "period": "monthly",
        "days": 30,
        "daily_credits": 0,
        "total_credits": 6000,
        "amount_sum": 300_000,
    }
}

MUTATION_ROUTES = [
    ("grant", {"tier": "standard", "period": "monthly", "reason": "paid externally"}),
    ("extend", {"extend_days": 30, "reason": "paid externally"}),
    ("adjust-credits", {"mode": "set", "credits": 100, "reason": "support credit"}),
    ("revoke", {"target": "pool", "reason": "refunded"}),
]


class InMemoryAudit:
    """Real claim/finalize semantics, including the duplicate-request-id guard."""

    def __init__(self):
        self.entries: list[dict] = []

    async def claim(self, *, request_id, action, target_user_id, operator, reason, params, source_ip=None, user_agent=None):
        existing = next(
            (e for e in self.entries if e["request_id"] == request_id), None
        )
        if existing is not None:
            raise AdminActionAlreadyRecorded(existing)
        entry = {
            "event_id": uuid.uuid4().hex,
            "request_id": request_id,
            "action": action,
            "target_user_id": target_user_id,
            "actor": {"operator": operator},
            "reason": reason,
            "params": params,
            "result": "in_progress",
            "created_at_ms": int(time.time() * 1000),
        }
        self.entries.append(entry)
        return entry["event_id"]

    async def finalize(self, event_id, *, result, before=None, after=None, error_code=None):
        for entry in self.entries:
            if entry["event_id"] == event_id:
                entry.update(result=result, before=before, after=after, error_code=error_code)
                return entry
        return None

    async def list_entries(self, **kwargs):
        items = [e for e in self.entries if not kwargs.get("user_id") or e["target_user_id"] == kwargs["user_id"]]
        return {
            "items": items,
            "total": len(items),
            "limit": kwargs.get("limit", 50),
            "offset": kwargs.get("offset", 0),
        }


@pytest.fixture
def client(super_admin_key: str):
    now_ms = int(time.time() * 1000)
    start_ms = now_ms - 6 * DAY_MS

    subscriptions = FakeCollection(
        [
            {
                "user_id": USER_ID,
                "tier": "standard",
                "period": "monthly",
                "days": 30,
                "daily_credits": 0,
                "total_credits": 6000,
                "credits_remaining": 6000,
                "start_ms": start_ms,
                "end_ms": start_ms + 30 * DAY_MS,
                "updated_at_ms": now_ms - 1000,
            }
        ]
    )
    users = FakeCollection([{"_id": USER_ID, "user_id": USER_ID, "username": "yvlfer"}])
    handler = FakeMongoHandler(
        subscriptions=subscriptions,
        daily_subscriptions=FakeCollection(),
        creditusage=FakeCollection(),
        users=users,
    )

    storage = SubscriptionStorage.__new__(SubscriptionStorage)
    storage.mongo_handler = handler
    storage.users_collection = "users"
    storage.subscriptions_collection = "subscriptions"
    storage.daily_subscriptions_collection = "daily_subscriptions"
    storage._indexes_ready = True

    rate_limit = RateLimitService.__new__(RateLimitService)
    rate_limit.mongo_handler = handler
    rate_limit.subscription_storage = storage

    async def computed_view(user_id: str):
        sub = await storage.get_raw_subscription(user_id)
        if not isinstance(sub, dict):
            return {"user_id": user_id, "active": False}
        remaining = await rate_limit._get_pool_credits_remaining(user_id, sub)
        return {
            "user_id": user_id,
            "active": int(sub["end_ms"]) > int(time.time() * 1000) and remaining > 0,
            "tier": sub.get("tier"),
            "end_ms": int(sub["end_ms"]),
            "credits_remaining": remaining,
        }

    audit = InMemoryAudit()
    service = AdminSubscriptionService(
        subscription_storage=storage,
        rate_limit_service=rate_limit,
        transaction_service=FakeTransactionService(CATALOG, computed_view),
        audit_service=audit,
    )

    app = make_admin_app(admin_subscriptions.router)
    app.dependency_overrides[get_admin_subscription_service] = lambda: service
    app.dependency_overrides[get_admin_audit_service] = lambda: audit

    test_client = TestClient(app)
    test_client.audit = audit
    test_client.subscriptions = subscriptions
    return test_client


def url(user_id: str = USER_ID, suffix: str = "") -> str:
    return f"{settings.API_PREFIX}/admin/subscriptions/{user_id}{suffix}"


# ---------------------------------------------------------------------------
# Auth matrix — parametrized so a new route cannot skip the guard
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("route,payload", MUTATION_ROUTES)
def test_mutations_require_the_super_admin_key(client, route, payload):
    response = client.post(url(suffix=f"/{route}"), json=payload)
    assert response.status_code == 401


@pytest.mark.parametrize("route,payload", MUTATION_ROUTES)
def test_mutations_reject_a_wrong_super_admin_key(client, route, payload):
    response = client.post(
        url(suffix=f"/{route}"),
        json=payload,
        headers={settings.SUPER_ADMIN_KEY_NAME.lower(): "nope"},
    )
    assert response.status_code == 403


@pytest.mark.parametrize("route,payload", MUTATION_ROUTES)
def test_mutations_require_an_operator(client, super_admin_key, route, payload):
    response = client.post(
        url(suffix=f"/{route}"),
        json=payload,
        headers={
            settings.SUPER_ADMIN_KEY_NAME.lower(): super_admin_key,
            settings.ADMIN_REQUEST_ID_HEADER_NAME.lower(): str(uuid.uuid4()),
        },
    )
    assert response.status_code == 400
    assert response.json()["error"]["code"] == "ADMIN_OPERATOR_REQUIRED"


@pytest.mark.parametrize("route,payload", MUTATION_ROUTES)
def test_mutations_require_a_uuid_request_id(client, super_admin_key, route, payload):
    response = client.post(
        url(suffix=f"/{route}"),
        json=payload,
        headers={
            settings.SUPER_ADMIN_KEY_NAME.lower(): super_admin_key,
            settings.ADMIN_OPERATOR_HEADER_NAME.lower(): "tester",
            settings.ADMIN_REQUEST_ID_HEADER_NAME.lower(): "not-a-uuid",
        },
    )
    assert response.status_code == 400
    assert response.json()["error"]["code"] == "ADMIN_REQUEST_ID_REQUIRED"


def test_diagnostic_needs_the_key_but_not_operator_or_request_id(
    client, super_admin_key
):
    assert client.get(url()).status_code == 401

    response = client.get(
        url(), headers={settings.SUPER_ADMIN_KEY_NAME.lower(): super_admin_key}
    )
    assert response.status_code == 200


# ---------------------------------------------------------------------------
# Diagnostic payload
# ---------------------------------------------------------------------------


def test_diagnostic_exposes_stored_computed_and_ledger(client, super_admin_key):
    response = client.get(
        url(), headers={settings.SUPER_ADMIN_KEY_NAME.lower(): super_admin_key}
    )
    body = response.json()

    assert body["user"]["exists"] is True
    assert body["raw"]["subscription"]["tier"] == "standard"
    assert body["computed"]["credits_remaining"] == 6000
    assert body["reconciliation"]["drift"] == 0
    assert "by_date" in body["credit_usage"]


def test_diagnostic_flags_a_string_typed_end_ms(client, super_admin_key):
    client.subscriptions.documents[0]["end_ms"] = str(
        client.subscriptions.documents[0]["end_ms"]
    )

    body = client.get(
        url(), headers={settings.SUPER_ADMIN_KEY_NAME.lower(): super_admin_key}
    ).json()

    codes = {warning["code"] for warning in body["warnings"]}
    assert "END_MS_NOT_INT" in codes


def test_diagnostic_on_an_unknown_user_reports_absence(client, super_admin_key):
    body = client.get(
        url("nobody"), headers={settings.SUPER_ADMIN_KEY_NAME.lower(): super_admin_key}
    ).json()

    assert body["user"]["exists"] is False
    assert body["raw"]["subscription"] is None


# ---------------------------------------------------------------------------
# Mutations
# ---------------------------------------------------------------------------


def test_adjust_credits_returns_before_and_after(client, super_admin_headers):
    response = client.post(
        url(suffix="/adjust-credits"),
        json={"mode": "set", "credits": 1234, "reason": "support credit"},
        headers=super_admin_headers,
    )

    assert response.status_code == 200
    body = response.json()
    assert body["success"] is True
    assert body["operator"] == "test-operator"
    assert body["before"]["computed"]["credits_remaining"] == 6000
    assert body["after"]["computed"]["credits_remaining"] == 1234


def test_reason_is_required(client, super_admin_headers):
    response = client.post(
        url(suffix="/adjust-credits"),
        json={"mode": "set", "credits": 100},
        headers=super_admin_headers,
    )
    assert response.status_code == 422


def test_extend_rejects_both_bounds_at_once(client, super_admin_headers):
    response = client.post(
        url(suffix="/extend"),
        json={"extend_days": 30, "new_end_ms": 123456789, "reason": "ambiguous"},
        headers=super_admin_headers,
    )
    assert response.status_code == 422


# ---------------------------------------------------------------------------
# Idempotency
# ---------------------------------------------------------------------------


def test_replaying_a_request_id_does_not_apply_twice(client, super_admin_headers):
    payload = {"mode": "delta", "credits": 500, "reason": "goodwill top-up"}

    first = client.post(
        url(suffix="/adjust-credits"), json=payload, headers=super_admin_headers
    )
    assert first.status_code == 200
    applied = first.json()["after"]["computed"]["credits_remaining"]

    second = client.post(
        url(suffix="/adjust-credits"), json=payload, headers=super_admin_headers
    )
    assert second.status_code == 409
    assert second.json()["error"]["code"] == "ADMIN_ACTION_DUPLICATE"

    # The balance must be untouched by the replay, and only one entry recorded.
    body = client.get(
        url(),
        headers={settings.SUPER_ADMIN_KEY_NAME.lower(): super_admin_headers[settings.SUPER_ADMIN_KEY_NAME.lower()]},
    ).json()
    assert body["computed"]["credits_remaining"] == applied
    assert len(client.audit.entries) == 1


def test_a_fresh_request_id_applies_again(client, super_admin_headers):
    payload = {"mode": "delta", "credits": 100, "reason": "goodwill top-up"}

    client.post(url(suffix="/adjust-credits"), json=payload, headers=super_admin_headers)
    headers = {
        **super_admin_headers,
        settings.ADMIN_REQUEST_ID_HEADER_NAME.lower(): str(uuid.uuid4()),
    }
    second = client.post(
        url(suffix="/adjust-credits"), json=payload, headers=headers
    )

    assert second.status_code == 200
    assert second.json()["after"]["computed"]["credits_remaining"] == 6200


def test_a_failed_action_is_still_audited(client, super_admin_headers):
    response = client.post(
        url("nobody", suffix="/extend"),
        json={"extend_days": 30, "reason": "no such subscription"},
        headers=super_admin_headers,
    )

    assert response.status_code == 404
    assert response.json()["error"]["code"] == "SUBSCRIPTION_NOT_FOUND"
    assert client.audit.entries[0]["result"] == "error"
    assert client.audit.entries[0]["error_code"] == "SUBSCRIPTION_NOT_FOUND"


def test_audit_feed_is_filterable(client, super_admin_headers, super_admin_key):
    client.post(
        url(suffix="/adjust-credits"),
        json={"mode": "set", "credits": 10, "reason": "support credit"},
        headers=super_admin_headers,
    )

    body = client.get(
        f"{settings.API_PREFIX}/admin/subscriptions/audit?user_id={USER_ID}",
        headers={settings.SUPER_ADMIN_KEY_NAME.lower(): super_admin_key},
    ).json()

    assert body["total"] == 1
    assert body["items"][0]["reason"] == "support credit"
