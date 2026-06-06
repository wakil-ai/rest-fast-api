"""Daily-pass tier identifiers, ranks, and quote helpers."""

from typing import Any

# Daily pass tiers (period must be ``daily``).
DAILY_PASS_TIERS = frozenset({"basic", "standard", "premium"})

# Legacy ``daily`` tier maps to basic rank; grandfathered credits stay on the document.
DAILY_PASS_TIER_RANK: dict[str, int] = {
    "basic": 1,
    "standard": 2,
    "premium": 3,
    "daily": 1,
}

# Highest daily-pass rank — no further upgrades possible.
DAILY_PASS_MAX_RANK = max(DAILY_PASS_TIER_RANK.values())


def normalize_daily_pass_tier(tier: str | None) -> str | None:
    """Map legacy ``daily`` to ``basic`` for comparisons; leave other tiers unchanged."""
    if tier == "daily":
        return "basic"
    return tier


def daily_pass_rank(tier: str | None) -> int:
    """Return rank for eligibility comparisons; unknown tiers rank 0."""
    if not tier:
        return 0
    return DAILY_PASS_TIER_RANK.get(tier, 0)


def is_daily_pass_quote(quote: Any) -> bool:
    """True when quote is a daily pass (basic/standard/premium + period daily)."""
    if not isinstance(quote, dict):
        return False
    if quote.get("period") != "daily":
        return False
    tier = quote.get("tier")
    return tier in DAILY_PASS_TIERS or tier == "daily"


def is_daily_pass_tier_period(tier: str | None, period: str | None) -> bool:
    return is_daily_pass_quote({"tier": tier, "period": period})
