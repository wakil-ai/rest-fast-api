"""Time-boxed subscription discount campaign.

Single source of truth for "is a promo active right now, and what does it do
to a given (tier, period) price". Deliberately evaluated per-call (not baked
in at service construction) so a campaign starts/stops purely from config —
no restart needed to end it, since `SUBSCRIPTION_PROMO_ENDS_AT` is checked
against wall-clock time on every quote.

See core.config for the SUBSCRIPTION_PROMO_* settings and
services.payments.base.BasePaymentService._get_subscription_quote for the
call site every payment rail (Payme/Click/Uzum/App Store/DT) funnels through.
"""

from dataclasses import dataclass, field
from datetime import datetime, timezone

from core.config import settings


@dataclass(frozen=True)
class PromoCampaign:
    percent: int
    ends_at_ms: int | None
    tiers: frozenset[str] = field(default_factory=frozenset)
    periods: frozenset[str] = field(default_factory=frozenset)

    def applies_to(self, tier: str, period: str) -> bool:
        return tier in self.tiers and period in self.periods


def _to_ms(value: datetime | None) -> int | None:
    if value is None:
        return None
    # Naive datetimes from env parsing are assumed UTC; aware ones are converted.
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return int(value.timestamp() * 1000)


def _split_csv(value: str) -> frozenset[str]:
    return frozenset(part.strip() for part in value.split(",") if part.strip())


def get_active_campaign(now_ms: int) -> PromoCampaign | None:
    """Return the active promo campaign, or None if disabled/out of window."""
    if not settings.SUBSCRIPTION_PROMO_ENABLED:
        return None

    percent = settings.SUBSCRIPTION_PROMO_PERCENT
    if percent <= 0 or percent >= 100:
        return None

    starts_at_ms = _to_ms(settings.SUBSCRIPTION_PROMO_STARTS_AT)
    ends_at_ms = _to_ms(settings.SUBSCRIPTION_PROMO_ENDS_AT)

    if starts_at_ms is not None and now_ms < starts_at_ms:
        return None
    if ends_at_ms is not None and now_ms >= ends_at_ms:
        return None

    tiers = _split_csv(settings.SUBSCRIPTION_PROMO_TIERS)
    periods = _split_csv(settings.SUBSCRIPTION_PROMO_PERIODS)
    if not tiers or not periods:
        return None

    return PromoCampaign(
        percent=percent,
        ends_at_ms=ends_at_ms,
        tiers=tiers,
        periods=periods,
    )


def apply_discount(
    tier: str,
    period: str,
    list_price_sum: int,
    now_ms: int,
) -> tuple[int, int | None, int | None, int | None]:
    """Return (effective_price_sum, list_price_sum, discount_percent, promo_ends_at_ms).

    The last three are None when no campaign applies to this (tier, period).
    Effective price is floored to the nearest 1,000 sum to keep it clean.
    """
    campaign = get_active_campaign(now_ms)
    if campaign is None or not campaign.applies_to(tier, period):
        return list_price_sum, None, None, None

    raw = list_price_sum * (100 - campaign.percent) // 100
    effective = (raw // 1_000) * 1_000
    if effective <= 0:
        return list_price_sum, None, None, None

    return effective, list_price_sum, campaign.percent, campaign.ends_at_ms
