"""Reconciliation math for manual subscription changes.

These drive the real ``RateLimitService`` and ``SubscriptionStorage`` against an
in-memory Mongo, so the ``total_credits`` ceiling is exercised for real. That
ceiling is the reason a Compass edit to ``credits_remaining`` grants nothing.
"""

import time

import pytest
from fastapi import HTTPException

from models.admin_subscription import (
    AdminAdjustCreditsRequest,
    AdminExtendRequest,
    AdminGrantRequest,
    AdminRevokeRequest,
)
from services.admin_subscription_service import AdminSubscriptionService
from services.rate_limit_service import RateLimitService
from services.subscription_storage import SubscriptionStorage
from tests.admin_fakes import (
    FakeCollection,
    FakeMongoHandler,
    FakeTransactionService,
)

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
    },
    ("pro", "monthly"): {
        "tier": "pro",
        "period": "monthly",
        "days": 30,
        "daily_credits": 0,
        "total_credits": 12000,
        "amount_sum": 600_000,
    },
    ("basic", "daily"): {
        "tier": "basic",
        "period": "daily",
        "days": 1,
        "daily_credits": 200,
        "total_credits": 200,
        "amount_sum": 15_000,
    },
}


def ms_to_date(ms: int) -> str:
    from datetime import datetime, timezone

    return datetime.fromtimestamp(ms / 1000, tz=timezone.utc).strftime("%Y-%m-%d")


class FakeAudit:
    def __init__(self):
        self.entries: list[dict] = []

    async def claim(self, **kwargs):
        event_id = f"event-{len(self.entries)}"
        self.entries.append({"event_id": event_id, "result": "in_progress", **kwargs})
        return event_id

    async def finalize(self, event_id, *, result, before=None, after=None, error_code=None):
        for entry in self.entries:
            if entry["event_id"] == event_id:
                entry.update(
                    result=result, before=before, after=after, error_code=error_code
                )
                return entry
        return None


@pytest.fixture
def harness():
    """Wire the real services onto an in-memory Mongo."""
    now_ms = int(time.time() * 1000)
    subscriptions = FakeCollection()
    daily = FakeCollection()
    creditusage = FakeCollection()
    users = FakeCollection([{"_id": USER_ID, "user_id": USER_ID}])

    handler = FakeMongoHandler(
        subscriptions=subscriptions,
        daily_subscriptions=daily,
        creditusage=creditusage,
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
        end_ms = int(sub.get("end_ms") or 0)
        return {
            "user_id": user_id,
            "active": end_ms > int(time.time() * 1000) and remaining > 0,
            "tier": sub.get("tier"),
            "end_ms": end_ms,
            "credits_remaining": remaining,
            "total_credits": int(sub.get("total_credits") or 0),
        }

    transaction = FakeTransactionService(CATALOG, computed_view)
    audit = FakeAudit()

    service = AdminSubscriptionService(
        subscription_storage=storage,
        rate_limit_service=rate_limit,
        transaction_service=transaction,
        audit_service=audit,
    )

    return {
        "now_ms": now_ms,
        "service": service,
        "storage": storage,
        "rate_limit": rate_limit,
        "transaction": transaction,
        "audit": audit,
        "subscriptions": subscriptions,
        "daily": daily,
        "creditusage": creditusage,
    }


def seed_subscription(harness, *, total=6000, remaining=6000, start_offset=-6 * DAY_MS, days=30):
    now_ms = harness["now_ms"]
    start_ms = now_ms + start_offset
    harness["subscriptions"].documents.append(
        {
            "user_id": USER_ID,
            "tier": "standard",
            "period": "monthly",
            "days": days,
            "daily_credits": 0,
            "total_credits": total,
            "credits_remaining": remaining,
            "start_ms": start_ms,
            "end_ms": start_ms + days * DAY_MS,
            "updated_at_ms": now_ms - 1000,
        }
    )
    return start_ms


def seed_usage(harness, ms: int, credits: int) -> None:
    harness["creditusage"].documents.append(
        {"user_id": USER_ID, "date": ms_to_date(ms), "credits_used": credits}
    )


def error_code(exc_info: pytest.ExceptionInfo) -> str:
    """Read the ``code`` from a plain HTTPException's dict-shaped detail."""
    detail = exc_info.value.detail
    assert isinstance(detail, dict), f"expected a dict detail, got {detail!r}"
    return detail["code"]


# ---------------------------------------------------------------------------
# The regression test for the production incident
# ---------------------------------------------------------------------------


async def test_adjust_set_makes_the_effective_balance_match(harness):
    """Setting 3000 with 4380 already spent must yield exactly 3000 spendable."""
    start_ms = seed_subscription(harness, total=6000, remaining=6000)
    seed_usage(harness, start_ms, 4380)

    await harness["service"].adjust_credits(
        USER_ID,
        AdminAdjustCreditsRequest(mode="set", credits=3000, reason="support credit"),
        operator="tester",
    )

    stored = await harness["storage"].get_raw_subscription(USER_ID)
    assert stored["total_credits"] == 7380  # ledger usage + desired balance
    assert stored["credits_remaining"] == 3000

    effective = await harness["rate_limit"]._get_pool_credits_remaining(USER_ID, stored)
    assert effective == 3000


async def test_writing_credits_remaining_alone_would_not_have_worked(harness):
    """Pins why the reconciliation exists: the ceiling wins."""
    start_ms = seed_subscription(harness, total=6000, remaining=6000)
    seed_usage(harness, start_ms, 4380)

    # The naive Compass edit.
    await harness["storage"].admin_set_pool_fields(
        USER_ID, updates={"credits_remaining": 3000}, now_ms=harness["now_ms"]
    )

    stored = await harness["storage"].get_raw_subscription(USER_ID)
    effective = await harness["rate_limit"]._get_pool_credits_remaining(USER_ID, stored)
    assert effective == 1620  # not 3000 — total_credits still caps it


async def test_adjust_delta_adds_to_the_effective_balance(harness):
    start_ms = seed_subscription(harness, total=6000, remaining=6000)
    seed_usage(harness, start_ms, 4380)

    await harness["service"].adjust_credits(
        USER_ID,
        AdminAdjustCreditsRequest(mode="delta", credits=1000, reason="goodwill top-up"),
        operator="tester",
    )

    stored = await harness["storage"].get_raw_subscription(USER_ID)
    effective = await harness["rate_limit"]._get_pool_credits_remaining(USER_ID, stored)
    assert effective == 2620  # 1620 computed + 1000


async def test_adjust_delta_clamps_at_zero(harness):
    start_ms = seed_subscription(harness, total=6000, remaining=6000)
    seed_usage(harness, start_ms, 4380)

    await harness["service"].adjust_credits(
        USER_ID,
        AdminAdjustCreditsRequest(mode="delta", credits=-99_999, reason="clawback"),
        operator="tester",
    )

    stored = await harness["storage"].get_raw_subscription(USER_ID)
    assert stored["credits_remaining"] == 0


async def test_adjust_rejects_an_inactive_subscription(harness):
    seed_subscription(harness, start_offset=-90 * DAY_MS)

    with pytest.raises(HTTPException) as excinfo:
        await harness["service"].adjust_credits(
            USER_ID,
            AdminAdjustCreditsRequest(mode="set", credits=100, reason="late fix"),
            operator="tester",
        )
    assert excinfo.value.status_code == 409
    assert error_code(excinfo) == "SUBSCRIPTION_NOT_ACTIVE"


# ---------------------------------------------------------------------------
# Grant
# ---------------------------------------------------------------------------


async def test_grant_after_current_preserves_the_carried_balance(harness):
    """`upsert_subscription` alone drops the carry-over; the admin path must not."""
    start_ms = seed_subscription(harness, total=6000, remaining=6000)
    seed_usage(harness, start_ms, 1000)
    before = await harness["service"]._computed_remaining(USER_ID)
    assert before == 5000

    await harness["service"].grant(
        USER_ID,
        AdminGrantRequest(
            tier="standard",
            period="monthly",
            start_mode="after_current",
            reason="paid externally",
        ),
        operator="tester",
    )

    stored = await harness["storage"].get_raw_subscription(USER_ID)
    effective = await harness["rate_limit"]._get_pool_credits_remaining(USER_ID, stored)
    assert effective == 11000  # 5000 carried + 6000 purchased


async def test_grant_now_closes_the_old_window_and_starts_fresh(harness):
    start_ms = seed_subscription(harness, total=6000, remaining=6000)
    seed_usage(harness, start_ms, 1000)

    await harness["service"].grant(
        USER_ID,
        AdminGrantRequest(
            tier="pro", period="monthly", start_mode="now", reason="upgrade"
        ),
        operator="tester",
    )

    stored = await harness["storage"].get_raw_subscription(USER_ID)
    effective = await harness["rate_limit"]._get_pool_credits_remaining(USER_ID, stored)
    assert effective == 12000
    assert stored["tier"] == "pro"


async def test_grant_rejects_an_unknown_plan(harness):
    with pytest.raises(HTTPException) as excinfo:
        await harness["service"].grant(
            USER_ID,
            AdminGrantRequest(tier="premium", period="yearly", reason="typo"),
            operator="tester",
        )
    assert excinfo.value.status_code == 400
    assert error_code(excinfo) == "INVALID_SUBSCRIPTION_PLAN"


async def test_grant_surfaces_eligibility_unless_overridden(harness):
    from models.payment import SubscriptionEligibilityError

    seed_subscription(harness)
    harness["transaction"].eligibility_error = SubscriptionEligibilityError(
        code="ACTIVE_SUBSCRIPTION_EXISTS", message="already active"
    )

    with pytest.raises(SubscriptionEligibilityError):
        await harness["service"].grant(
            USER_ID,
            AdminGrantRequest(tier="standard", period="monthly", reason="dup"),
            operator="tester",
        )

    result = await harness["service"].grant(
        USER_ID,
        AdminGrantRequest(
            tier="standard",
            period="monthly",
            reason="paid externally, override intended",
            override_eligibility=True,
        ),
        operator="tester",
    )
    assert result["overridden_eligibility_code"] == "ACTIVE_SUBSCRIPTION_EXISTS"


async def test_daily_pass_grant_creates_a_lot(harness):
    await harness["service"].grant(
        USER_ID,
        AdminGrantRequest(tier="basic", period="daily", reason="paid externally"),
        operator="tester",
    )

    lots = await harness["storage"].get_all_daily_lots(USER_ID)
    assert len(lots) == 1
    assert lots[0]["provider"] == "admin"
    assert lots[0]["credits_remaining"] == 200
    # The pool document must be untouched by a daily-pass grant.
    assert await harness["storage"].get_raw_subscription(USER_ID) is None


# ---------------------------------------------------------------------------
# Extend
# ---------------------------------------------------------------------------


async def test_extend_preserves_the_effective_balance(harness):
    """Widening the window pulls in older ledger rows; the balance must not move."""
    start_ms = seed_subscription(harness, total=6000, remaining=6000, days=5)
    seed_usage(harness, start_ms, 500)
    # Usage on a day beyond the original window, which extending would absorb.
    seed_usage(harness, start_ms + 20 * DAY_MS, 2000)

    before = await harness["service"]._computed_remaining(USER_ID)

    await harness["service"].extend(
        USER_ID,
        AdminExtendRequest(extend_days=30, reason="external renewal"),
        operator="tester",
    )

    after = await harness["service"]._computed_remaining(USER_ID)
    assert after == before


async def test_extend_pushes_end_ms_out(harness):
    seed_subscription(harness)
    before = await harness["storage"].get_raw_subscription(USER_ID)

    await harness["service"].extend(
        USER_ID,
        AdminExtendRequest(extend_days=30, reason="external renewal"),
        operator="tester",
    )

    after = await harness["storage"].get_raw_subscription(USER_ID)
    assert after["end_ms"] == before["end_ms"] + 30 * DAY_MS


async def test_extend_requires_a_subscription(harness):
    with pytest.raises(HTTPException) as excinfo:
        await harness["service"].extend(
            USER_ID,
            AdminExtendRequest(extend_days=30, reason="nothing to extend"),
            operator="tester",
        )
    assert excinfo.value.status_code == 404
    assert error_code(excinfo) == "SUBSCRIPTION_NOT_FOUND"


async def test_extend_detects_a_concurrent_write(harness):
    seed_subscription(harness)
    # Simulate another writer moving the document between read and write.
    harness["subscriptions"].documents[0]["updated_at_ms"] = 999_999_999_999

    service = harness["service"]
    original = harness["storage"].get_raw_subscription

    async def stale_read(user_id):
        document = await original(user_id)
        document["updated_at_ms"] = 1  # a value no longer on disk
        return document

    harness["storage"].get_raw_subscription = stale_read

    with pytest.raises(HTTPException) as excinfo:
        await service.extend(
            USER_ID,
            AdminExtendRequest(extend_days=30, reason="racy"),
            operator="tester",
        )
    assert excinfo.value.status_code == 409
    assert error_code(excinfo) == "ADMIN_SUBSCRIPTION_CONFLICT"


# ---------------------------------------------------------------------------
# Revoke
# ---------------------------------------------------------------------------


async def test_revoke_expires_without_deleting(harness):
    seed_subscription(harness)

    await harness["service"].revoke(
        USER_ID,
        AdminRevokeRequest(target="pool", reason="refunded"),
        operator="tester",
    )

    stored = await harness["storage"].get_raw_subscription(USER_ID)
    assert stored is not None  # history stays readable
    # Against the current clock, not the fixture's: revoke stamps end_ms from its
    # own now_ms, which is a millisecond or two later than the harness captured.
    assert stored["end_ms"] < int(time.time() * 1000)
    assert stored["credits_remaining"] == 0
    assert stored["revoked_by"] == "tester"

    effective = await harness["rate_limit"]._get_active_pool_subscription(USER_ID)
    assert effective is None


async def test_revoke_then_grant_is_a_clean_reset(harness):
    start_ms = seed_subscription(harness, total=6000, remaining=6000)
    seed_usage(harness, start_ms, 4380)

    await harness["service"].revoke(
        USER_ID, AdminRevokeRequest(target="pool", reason="reset"), operator="tester"
    )
    await harness["service"].grant(
        USER_ID,
        AdminGrantRequest(tier="standard", period="monthly", reason="re-grant"),
        operator="tester",
    )

    stored = await harness["storage"].get_raw_subscription(USER_ID)
    effective = await harness["rate_limit"]._get_pool_credits_remaining(USER_ID, stored)
    assert effective == 6000


# ---------------------------------------------------------------------------
# Type safety — a String end_ms silently breaks credit consumption
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "action,payload",
    [
        ("adjust_credits", AdminAdjustCreditsRequest(mode="set", credits=100, reason="typed-write check")),
        ("extend", AdminExtendRequest(extend_days=5, reason="typed-write check")),
        ("revoke", AdminRevokeRequest(target="pool", reason="typed-write check")),
    ],
)
async def test_admin_writes_are_int_typed(harness, action, payload):
    seed_subscription(harness)

    await getattr(harness["service"], action)(USER_ID, payload, operator="tester")

    stored = await harness["storage"].get_raw_subscription(USER_ID)
    for field in ("start_ms", "end_ms", "total_credits", "credits_remaining"):
        assert isinstance(stored[field], int), f"{field} must stay an int"
        assert not isinstance(stored[field], bool)


async def test_string_typed_end_ms_is_repaired_on_write(harness):
    """A doc already damaged by a manual edit comes back int-typed."""
    seed_subscription(harness)
    harness["subscriptions"].documents[0]["end_ms"] = str(
        harness["subscriptions"].documents[0]["end_ms"]
    )

    await harness["service"].extend(
        USER_ID,
        AdminExtendRequest(new_end_ms=harness["now_ms"] + 30 * DAY_MS, reason="repair"),
        operator="tester",
    )

    stored = await harness["storage"].get_raw_subscription(USER_ID)
    assert isinstance(stored["end_ms"], int)
