"""The subscription period vocabulary and the multi-month plans.

`quarterly` (3 months) and `semiannual` (6 months) sit between monthly and
yearly. Two things about them are easy to get wrong and invisible if they are:

* `get_subscription_catalog` used to iterate a hardcoded period tuple, so a plan
  could exist in the catalog, price correctly, and be grantable by an admin —
  yet never be offered for sale.
* Uzum plan ids are `"<tier>_<period>"` split on the last underscore, so a period
  slug containing an underscore silently reassigns part of it to the tier.
"""

from typing import get_args

import pytest

from core.config import settings
from core.subscription_tiers import SUBSCRIPTION_PERIODS
from models.payment import SubscriptionPeriod
from services.payments.base import BasePaymentService, build_subscription_catalog
from services.payments.uzum import UzumService

# tier, period, days, credits, price multiple of the tier's monthly price
MULTI_MONTH_PLANS = [
    ("standard", "quarterly", 90, 18_000, 3),
    ("standard", "semiannual", 180, 36_000, 6),
    ("pro", "quarterly", 90, 36_000, 3),
    ("pro", "semiannual", 180, 72_000, 6),
]


@pytest.fixture
def service() -> BasePaymentService:
    """A catalog-only service — no Mongo, no rate limiter."""
    instance = BasePaymentService.__new__(BasePaymentService)
    instance._subscription_catalog = build_subscription_catalog()
    return instance


@pytest.fixture(autouse=True)
def _promo_disabled():
    """Price assertions here are about list prices, not campaign prices."""
    original = settings.SUBSCRIPTION_PROMO_ENABLED
    settings.SUBSCRIPTION_PROMO_ENABLED = False
    yield
    settings.SUBSCRIPTION_PROMO_ENABLED = original


def test_period_literal_matches_the_period_tuple():
    """The Pydantic Literal can't be generated from the tuple, so it can drift.

    Drift is silent in the worst direction: the catalog offers a plan the request
    model then rejects with a 422.
    """
    assert set(get_args(SubscriptionPeriod)) == set(SUBSCRIPTION_PERIODS)


def test_periods_contain_no_underscore():
    """Uzum's `rsplit("_", 1)` misparses any period slug with an underscore."""
    assert [period for period in SUBSCRIPTION_PERIODS if "_" in period] == []


@pytest.mark.parametrize("tier,period,days,credits,_multiple", MULTI_MONTH_PLANS)
def test_multi_month_plans_are_offered_for_sale(
    service, tier, period, days, credits, _multiple
):
    """The regression test for the hardcoded catalog loop."""
    plans = service.get_subscription_catalog()

    plan = next(
        (p for p in plans if p["tier"] == tier and p["period"] == period), None
    )
    assert plan is not None, f"{tier}/{period} is missing from the catalog"
    assert plan["days"] == days
    assert plan["total_credits"] == credits
    # Pool tiers have no per-day cap.
    assert plan["daily_credits"] == 0


@pytest.mark.parametrize("tier,period,_days,credits,_multiple", MULTI_MONTH_PLANS)
def test_credits_scale_strictly_with_duration(
    service, tier, period, _days, credits, _multiple
):
    """The tier fixes the monthly credit rate; the period only buys more months."""
    monthly = service._get_subscription_quote(tier, "monthly")
    months = service._get_subscription_quote(tier, period)["days"] // 30

    assert credits == monthly["total_credits"] * months


@pytest.mark.parametrize("tier,period,_days,_credits,multiple", MULTI_MONTH_PLANS)
def test_list_prices_are_the_agreed_multiples(
    service, tier, period, _days, _credits, multiple
):
    """Quarterly is 3x monthly (no discount); semiannual is 5x (one month free)."""
    price_multiple = 3 if period == "quarterly" else 5
    monthly_price = service._get_list_price_sum(tier, "monthly")

    assert service._get_list_price_sum(tier, period) == monthly_price * price_multiple


@pytest.mark.parametrize("tier", ["standard", "pro"])
def test_semiannual_grants_six_months_for_five_months_of_price(service, tier):
    """The free month is real: six months of both window and credits."""
    monthly = service._get_subscription_quote(tier, "monthly")
    semiannual = service._get_subscription_quote(tier, "semiannual")

    assert semiannual["days"] == monthly["days"] * 6
    assert semiannual["total_credits"] == monthly["total_credits"] * 6
    assert semiannual["amount_sum"] == monthly["amount_sum"] * 5


@pytest.mark.parametrize("tier,period,_d,_c,_m", MULTI_MONTH_PLANS)
def test_uzum_plan_ids_round_trip(tier, period, _d, _c, _m):
    uzum = UzumService.__new__(UzumService)

    assert uzum._parse_plan_id(f"{tier}_{period}") == (tier, period)


def test_catalog_is_ordered_shortest_period_first(service):
    """Plan pickers render in catalog order, so duration order is the useful one."""
    plans = [p for p in service.get_subscription_catalog() if p["tier"] == "standard"]

    assert [plan["days"] for plan in plans] == sorted(plan["days"] for plan in plans)


def test_multi_month_plans_are_discountable(service):
    """The new periods ship inside SUBSCRIPTION_PROMO_PERIODS, unlike daily passes."""
    from core.subscription_promo import _split_csv

    periods = _split_csv(settings.SUBSCRIPTION_PROMO_PERIODS)

    assert {"quarterly", "semiannual"} <= periods
    assert "daily" not in periods
