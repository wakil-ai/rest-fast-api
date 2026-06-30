"""``BasePaymentService.get_user_subscription`` surfaces ``can_upload``.

The subscription endpoint (``GET /payme/subscriptions/{user_id}``) is the
frontend's source of upload eligibility. To avoid extra DB round-trips it derives
``can_upload`` *inline* from data it already holds — the subscription doc, the
daily-pass doc, and the ``credit_status`` dict — via the shared pure predicate
``RateLimitService.is_upload_entitled``. The decision must match that gate and be
present on BOTH response paths (no-subscription and active-subscription).
"""

import time
from unittest.mock import AsyncMock

from models.payment import UserSubscriptionResponse
from services.payments.base import BasePaymentService
from services.rate_limit_service import RateLimitService

_FUTURE_MS = int(time.time() * 1000) + 7 * 24 * 60 * 60 * 1000  # +7 days


def _credit_status(
    *,
    on_signup_bonus=False,
    has_daily_pass_credits=False,
    has_active_promo=False,
    effective_daily_credit_limit=100,
):
    return {
        "effective_daily_credit_limit": effective_daily_credit_limit,
        "today_credits_used": 10,
        "remaining_credits": 90,
        "uses_combined_credit_pool": True,
        "on_signup_bonus": on_signup_bonus,
        "has_daily_pass_credits": has_daily_pass_credits,
        "has_active_promo": has_active_promo,
    }


def _make_payment_service(*, subscription, credit_status, daily_pass=None):
    """BasePaymentService whose rate_limit_service uses the REAL pure predicate.

    Only ``get_credit_status`` is mocked; ``is_upload_entitled`` runs for real so
    the test exercises the actual eligibility derivation, not a stub.
    """
    service = BasePaymentService.__new__(BasePaymentService)

    service.subscription_storage = AsyncMock()
    service.subscription_storage.get_subscription = AsyncMock(return_value=subscription)
    service.subscription_storage.get_daily_subscription = AsyncMock(
        return_value=daily_pass
    )

    rls = RateLimitService.__new__(RateLimitService)
    rls.get_credit_status = AsyncMock(return_value=credit_status)
    service.rate_limit_service = rls
    return service


async def test_no_subscription_signup_bonus_user_can_upload():
    # First-time user: no subscription, still on the welcome pool.
    service = _make_payment_service(
        subscription=None,
        credit_status=_credit_status(on_signup_bonus=True),
    )
    data = await service.get_user_subscription("u1")

    assert data["active"] is False  # confirms the no-subscription path
    assert data["can_upload"] is True
    assert UserSubscriptionResponse(**data).can_upload is True  # flows through **data


async def test_no_subscription_exhausted_bonus_cannot_upload():
    service = _make_payment_service(
        subscription=None,
        credit_status=_credit_status(on_signup_bonus=False),
    )
    data = await service.get_user_subscription("u1")

    assert data["active"] is False
    assert data["can_upload"] is False


def _daily_pass_doc():
    return {
        "tier": "basic",
        "period": "daily",
        "daily_credits": 200,
        "start_ms": _FUTURE_MS - 24 * 60 * 60 * 1000,
        "end_ms": _FUTURE_MS,
    }


async def test_daily_pass_with_credits_can_upload():
    # A user whose only entitlement is a daily pass *with credits remaining*
    # may upload. Eligibility tracks remaining credits, not just an open window.
    service = _make_payment_service(
        subscription=None,
        credit_status=_credit_status(has_daily_pass_credits=True),
        daily_pass=_daily_pass_doc(),
    )
    data = await service.get_user_subscription("u1")

    assert data["active"] is False
    assert data["daily_pass_active"] is True
    assert data["can_upload"] is True


async def test_exhausted_daily_pass_cannot_upload():
    # Regression: active daily-pass window but today's credits are spent. The
    # gate (can_upload_files) denies on remaining == 0, so the endpoint must too —
    # even though daily_pass_active (the window) is still True.
    service = _make_payment_service(
        subscription=None,
        credit_status=_credit_status(has_daily_pass_credits=False),
        daily_pass=_daily_pass_doc(),
    )
    data = await service.get_user_subscription("u1")

    assert data["daily_pass_active"] is True  # window still open
    assert data["can_upload"] is False  # but no credits left to spend


async def test_active_promo_user_can_upload():
    # Any active (non-expired) promo grants upload, surfaced via has_active_promo.
    service = _make_payment_service(
        subscription=None,
        credit_status=_credit_status(on_signup_bonus=False, has_active_promo=True),
    )
    data = await service.get_user_subscription("u1")

    assert data["can_upload"] is True


async def test_active_subscription_includes_can_upload():
    sub = {
        "tier": "pro",
        "period": "monthly",
        "start_ms": _FUTURE_MS - 30 * 24 * 60 * 60 * 1000,
        "end_ms": _FUTURE_MS,
        "total_credits": 200,
        "credits_remaining": 90,
        "daily_credits": 0,
    }
    service = _make_payment_service(
        subscription=sub,
        credit_status=_credit_status(on_signup_bonus=False),
    )
    data = await service.get_user_subscription("u1")

    assert data["active"] is True  # confirms the active-subscription path
    assert data["can_upload"] is True


async def test_can_upload_reuses_single_credit_status_call():
    # Single source of truth: derivation reuses the one get_credit_status call.
    service = _make_payment_service(
        subscription=None,
        credit_status=_credit_status(on_signup_bonus=True),
    )
    await service.get_user_subscription("u1")
    service.rate_limit_service.get_credit_status.assert_awaited_once_with("u1")
