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

# tier, period, days, credits, percent off the straight monthly rate
MULTI_MONTH_PLANS = [
    ("standard", "quarterly", 90, 18_000, 10),
    ("standard", "semiannual", 180, 36_000, 15),
    ("pro", "quarterly", 90, 36_000, 10),
    ("pro", "semiannual", 180, 72_000, 15),
]

# Every pool plan, longest-lived last, with its discount off n x monthly.
COMMITMENT_DISCOUNTS = [
    ("monthly", 1, 0),
    ("quarterly", 3, 10),
    ("semiannual", 6, 15),
    ("yearly", 12, 20),
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


@pytest.mark.parametrize("tier,period,days,credits,_discount", MULTI_MONTH_PLANS)
def test_multi_month_plans_are_offered_for_sale(
    service, tier, period, days, credits, _discount
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


@pytest.mark.parametrize("tier,period,_days,credits,_discount", MULTI_MONTH_PLANS)
def test_credits_scale_strictly_with_duration(
    service, tier, period, _days, credits, _discount
):
    """The tier fixes the monthly credit rate; the period only buys more months."""
    monthly = service._get_subscription_quote(tier, "monthly")
    months = service._get_subscription_quote(tier, period)["days"] // 30

    assert credits == monthly["total_credits"] * months


@pytest.mark.parametrize("tier", ["standard", "pro"])
@pytest.mark.parametrize("period,months,discount", COMMITMENT_DISCOUNTS)
def test_list_price_applies_the_commitment_discount(
    service, tier, period, months, discount
):
    """Longer commitments discount the straight monthly rate: -10/-15/-20%."""
    monthly = service._get_list_price_sum(tier, "monthly")
    expected = round(monthly * months * (100 - discount) / 100)

    assert service._get_list_price_sum(tier, period) == expected


@pytest.mark.parametrize("tier", ["standard", "pro"])
def test_value_per_credit_improves_with_every_step_up(service, tier):
    """The ladder must stay monotonic.

    If a longer plan ever costs more per credit than a shorter one it is pure
    downside for the customer, and the longest plan stops being worth buying —
    which is exactly what happened when 6-month and yearly were priced at the
    same rate.
    """
    rates = []
    for period, _months, _discount in COMMITMENT_DISCOUNTS:
        quote = service._get_subscription_quote(tier, period)
        rates.append(quote["amount_sum"] / quote["total_credits"])

    assert rates == sorted(rates, reverse=True)
    assert len(set(rates)) == len(rates), "two plans offer identical value per credit"


@pytest.mark.parametrize("tier", ["standard", "pro"])
def test_yearly_beats_buying_two_six_month_plans(service, tier):
    """Otherwise the longest commitment is money for nothing."""
    semiannual = service._get_list_price_sum(tier, "semiannual")
    yearly = service._get_list_price_sum(tier, "yearly")

    assert yearly < semiannual * 2


@pytest.mark.parametrize("tier,period,_d,_c,_disc", MULTI_MONTH_PLANS)
def test_uzum_plan_ids_round_trip(tier, period, _d, _c, _disc):
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
