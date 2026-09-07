"""Subscription promo discount campaign.

Covers core.subscription_promo directly, and its wiring into
BasePaymentService._get_subscription_quote / get_subscription_catalog /
init_payment — the single chokepoint every payment rail (Payme, Click, Uzum,
App Store, DT) funnels through. See docs/howto-configure-payments.md for the
SUBSCRIPTION_PROMO_* env vars that drive this.
"""

import time
from datetime import datetime, timedelta, timezone

import pytest

from core.config import settings
from models.payment import SubscriptionEligibilityError
from services.payments.base import BasePaymentService, build_subscription_catalog
from services.payments.uzum import UzumService

_NOW_MS = int(time.time() * 1000)
_DAY_MS = 24 * 60 * 60 * 1000


@pytest.fixture(autouse=True)
def _reset_promo_settings():
    """Every test starts from the shipped-disabled default and restores it after."""
    original = {
        # These tests are about what a campaign does to daily passes, so the
        # separate product switch is held on — otherwise they would only be
        # re-testing DAILY_PASS_ENABLED.
        "DAILY_PASS_ENABLED": settings.DAILY_PASS_ENABLED,
        "SUBSCRIPTION_PROMO_ENABLED": settings.SUBSCRIPTION_PROMO_ENABLED,
        "SUBSCRIPTION_PROMO_PERCENT": settings.SUBSCRIPTION_PROMO_PERCENT,
        "SUBSCRIPTION_PROMO_STARTS_AT": settings.SUBSCRIPTION_PROMO_STARTS_AT,
        "SUBSCRIPTION_PROMO_ENDS_AT": settings.SUBSCRIPTION_PROMO_ENDS_AT,
        "SUBSCRIPTION_PROMO_TIERS": settings.SUBSCRIPTION_PROMO_TIERS,
        "SUBSCRIPTION_PROMO_PERIODS": settings.SUBSCRIPTION_PROMO_PERIODS,
    }
    settings.DAILY_PASS_ENABLED = True
    settings.SUBSCRIPTION_PROMO_ENABLED = False
    settings.SUBSCRIPTION_PROMO_PERCENT = 0
    settings.SUBSCRIPTION_PROMO_STARTS_AT = None
    settings.SUBSCRIPTION_PROMO_ENDS_AT = None
    yield
    for key, value in original.items():
        setattr(settings, key, value)


def _enable_campaign(*, percent=35, ends_in_ms=30 * _DAY_MS, tiers="standard,pro",
                      periods="monthly,yearly"):
    settings.SUBSCRIPTION_PROMO_ENABLED = True
    settings.SUBSCRIPTION_PROMO_PERCENT = percent
    settings.SUBSCRIPTION_PROMO_STARTS_AT = None
    settings.SUBSCRIPTION_PROMO_ENDS_AT = datetime.fromtimestamp(
        (_NOW_MS + ends_in_ms) / 1000, tz=timezone.utc
    )
    settings.SUBSCRIPTION_PROMO_TIERS = tiers
    settings.SUBSCRIPTION_PROMO_PERIODS = periods


def _plan(plans: list[dict], tier: str, period: str) -> dict:
    return next(p for p in plans if p["tier"] == tier and p["period"] == period)


# --------------------------------------------------------------------------
# 1. Disabled by default
# --------------------------------------------------------------------------


def test_promo_disabled_catalog_has_list_prices_only():
    service = BasePaymentService.__new__(BasePaymentService)
    service._subscription_catalog = _real_catalog()

    plans = service.get_subscription_catalog()

    standard_monthly = _plan(plans, "standard", "monthly")
    assert standard_monthly["amount_sum"] == 300_000
    assert standard_monthly["list_price_sum"] is None
    assert standard_monthly["discount_percent"] is None
    assert standard_monthly["promo_ends_at_ms"] is None


# --------------------------------------------------------------------------
# 2. Active campaign discounts standard/pro monthly+yearly, withdraws daily
# --------------------------------------------------------------------------


def test_promo_active_discounts_monthly_and_yearly_only():
    _enable_campaign()
    service = BasePaymentService.__new__(BasePaymentService)
    service._subscription_catalog = _real_catalog()

    plans = service.get_subscription_catalog()

    assert _plan(plans, "standard", "monthly")["amount_sum"] == 195_000
    assert _plan(plans, "pro", "monthly")["amount_sum"] == 390_000
    assert _plan(plans, "standard", "yearly")["amount_sum"] == 1_872_000
    assert _plan(plans, "pro", "yearly")["amount_sum"] == 3_744_000

    for plan in (
        _plan(plans, "standard", "monthly"),
        _plan(plans, "pro", "yearly"),
    ):
        assert plan["discount_percent"] == 35
        assert plan["list_price_sum"] is not None

    # Daily passes are withdrawn from the catalog outright while the sale runs,
    # so the plan pickers have no daily cycle to offer.
    assert [plan for plan in plans if plan["period"] == "daily"] == []


# --------------------------------------------------------------------------
# 2b. The daily kill-switch is scoped to an active campaign only
# --------------------------------------------------------------------------


def test_daily_passes_listed_while_promo_disabled():
    service = BasePaymentService.__new__(BasePaymentService)
    service._subscription_catalog = _real_catalog()

    plans = service.get_subscription_catalog()

    for tier, price in (("basic", 15_000), ("standard", 30_000), ("premium", 50_000)):
        daily_plan = _plan(plans, tier, "daily")
        assert daily_plan["amount_sum"] == price
        assert daily_plan["discount_percent"] is None


def test_daily_passes_return_once_campaign_expires():
    _enable_campaign(ends_in_ms=_DAY_MS)
    service = BasePaymentService.__new__(BasePaymentService)
    service._subscription_catalog = _real_catalog()

    assert [plan for plan in service.get_subscription_catalog()
            if plan["period"] == "daily"] == []

    # Same cached service instance, campaign now in the past — no restart.
    settings.SUBSCRIPTION_PROMO_ENDS_AT = datetime.fromtimestamp(
        (_NOW_MS - _DAY_MS) / 1000, tz=timezone.utc
    )

    assert _plan(service.get_subscription_catalog(), "basic", "daily")["amount_sum"] == 15_000


@pytest.mark.asyncio
async def test_init_payment_rejects_daily_pass_during_campaign():
    _enable_campaign()
    service = _make_service()

    with pytest.raises(SubscriptionEligibilityError) as excinfo:
        await service.init_payment(
            amount_sum=None,
            user_id="u1",
            callback_url="https://example.com/cb",
            subscription_tier="basic",
            subscription_period="daily",
        )

    assert excinfo.value.code == "DAILY_PASS_DISABLED_DURING_PROMO"
    assert service.db_handler.inserted == []


@pytest.mark.asyncio
async def test_init_payment_allows_daily_pass_once_campaign_expires():
    service = _make_service()

    result = await service.init_payment(
        amount_sum=None,
        user_id="u1",
        callback_url="https://example.com/cb",
        subscription_tier="basic",
        subscription_period="daily",
    )

    assert result["order_id"]


# --------------------------------------------------------------------------
# 3. Expiry is evaluated live — no restart needed
# --------------------------------------------------------------------------


def test_promo_expires_without_restart():
    service = BasePaymentService.__new__(BasePaymentService)
    service._subscription_catalog = _real_catalog()

    _enable_campaign(ends_in_ms=_DAY_MS)  # ends tomorrow
    active_quote = service._get_subscription_quote("pro", "yearly")
    assert active_quote["amount_sum"] == 3_744_000

    # Same cached service instance, campaign now in the past.
    settings.SUBSCRIPTION_PROMO_ENDS_AT = datetime.fromtimestamp(
        (_NOW_MS - _DAY_MS) / 1000, tz=timezone.utc
    )
    expired_quote = service._get_subscription_quote("pro", "yearly")
    assert expired_quote["amount_sum"] == 5_760_000
    assert expired_quote["discount_percent"] is None


# --------------------------------------------------------------------------
# 4. init_payment charges the discounted amount and rejects the list price
# --------------------------------------------------------------------------


class _FakeDb:
    def __init__(self):
        self.inserted: list[tuple[str, dict]] = []

    async def find_one(self, collection, query):
        return None

    async def insert_one(self, collection, document):
        self.inserted.append((collection, document))


def _make_service():
    service = BasePaymentService.__new__(BasePaymentService)
    service._subscription_catalog = _real_catalog()
    service.provider = "test-provider"
    service.invoices_collection = "payment_invoices"
    service.db_handler = _FakeDb()
    service._invoice_indexes_ready = True

    async def _get_user_by_id(user_id):
        return {"user_id": user_id}

    service.get_user_by_id = _get_user_by_id

    class _Storage:
        async def get_subscription(self, user_id):
            return None

        async def get_daily_subscription(self, user_id):
            return None

    service.subscription_storage = _Storage()

    async def _build_payment_link(*, amount_sum, user_id, callback_url, order_id):
        return f"https://pay.example.com/{order_id}"

    service.build_payment_link = _build_payment_link
    return service


@pytest.mark.asyncio
async def test_init_payment_charges_discounted_amount():
    _enable_campaign()
    service = _make_service()

    result = await service.init_payment(
        amount_sum=None,
        user_id="u1",
        callback_url="https://example.com/cb",
        subscription_tier="pro",
        subscription_period="yearly",
    )

    assert result["order_id"]
    _, document = service.db_handler.inserted[0]
    assert document["amount_sum"] == 3_744_000
    assert document["subscription"]["list_price_sum"] == 5_760_000
    assert document["subscription"]["discount_percent"] == 35


@pytest.mark.asyncio
async def test_init_payment_rejects_stale_list_price():
    _enable_campaign()
    service = _make_service()

    with pytest.raises(ValueError, match="does not match"):
        await service.init_payment(
            amount_sum=5_760_000,  # list price — no longer valid once the promo is live
            user_id="u1",
            callback_url="https://example.com/cb",
            subscription_tier="pro",
            subscription_period="yearly",
        )


# --------------------------------------------------------------------------
# 5. An invoice frozen during the campaign still finalizes at the discounted
#    amount after the campaign has since expired (the invoice document stores
#    its own quote snapshot; _finalize_subscription_invoice never re-derives it).
# --------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_invoice_created_during_campaign_keeps_discounted_amount_after_expiry():
    _enable_campaign(ends_in_ms=_DAY_MS)
    service = _make_service()

    result = await service.init_payment(
        amount_sum=None,
        user_id="u1",
        callback_url="https://example.com/cb",
        subscription_tier="standard",
        subscription_period="monthly",
    )
    _, invoice_document = service.db_handler.inserted[0]

    # Campaign lapses before the customer finishes paying.
    settings.SUBSCRIPTION_PROMO_ENDS_AT = datetime.fromtimestamp(
        (_NOW_MS - _DAY_MS) / 1000, tz=timezone.utc
    )

    assert invoice_document["amount_sum"] == 195_000
    assert result["order_id"] == invoice_document["order_id"]


# --------------------------------------------------------------------------
# 6. Uzum /create accepts either the effective or the list price, because it
#    recomputes the quote fresh on every callback (unlike Payme/Click, which
#    compare against an already-frozen invoice amount).
# --------------------------------------------------------------------------


def test_uzum_get_list_price_ignores_active_campaign():
    _enable_campaign()
    service = UzumService.__new__(UzumService)
    service._subscription_catalog = _real_catalog()

    assert service._get_list_price_sum("pro", "yearly") == 5_760_000
    assert service._get_subscription_quote("pro", "yearly")["amount_sum"] == 3_744_000


def _real_catalog() -> dict:
    """The real catalog table, built straight from settings.

    Previously a hand-copied duplicate, which meant a new plan could be added to
    the shipped catalog and these tests would never see it.
    """
    return build_subscription_catalog()
