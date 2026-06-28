"""Tests that ``BasePaymentService.get_user_subscription`` surfaces ``can_upload``.

The subscription endpoint (``GET /payme/subscriptions/{user_id}``) is the
frontend's source of upload eligibility. The decision must mirror the backend
gate (``RateLimitService.can_upload_files``) and be present on BOTH response
paths: the no-subscription path (where first-time signup-bonus users land) and
the active-subscription path.
"""

import time
from unittest.mock import AsyncMock

from models.payment import UserSubscriptionResponse
from services.payments.base import BasePaymentService

_FUTURE_MS = int(time.time() * 1000) + 7 * 24 * 60 * 60 * 1000  # +7 days

_CREDIT_STATUS = {
    "effective_daily_credit_limit": 100,
    "today_credits_used": 10,
    "remaining_credits": 90,
    "uses_combined_credit_pool": True,
}


def _make_payment_service(*, subscription, can_upload, daily_pass=None):
    """Build a BasePaymentService with mocked storage/rate-limit deps."""
    service = BasePaymentService.__new__(BasePaymentService)

    service.subscription_storage = AsyncMock()
    service.subscription_storage.get_subscription = AsyncMock(return_value=subscription)
    service.subscription_storage.get_daily_subscription = AsyncMock(
        return_value=daily_pass
    )

    service.rate_limit_service = AsyncMock()
    service.rate_limit_service.get_credit_status = AsyncMock(
        return_value=dict(_CREDIT_STATUS)
    )
    service.rate_limit_service.can_upload_files = AsyncMock(return_value=can_upload)
    return service


async def test_no_subscription_user_can_upload_true():
    # First-time signup-bonus user: no subscription, gate says yes.
    service = _make_payment_service(subscription=None, can_upload=True)
    data = await service.get_user_subscription("u1")

    assert data["active"] is False  # confirms we took the no-subscription path
    assert data["can_upload"] is True
    # And it flows through the response model (the **data splat).
    assert UserSubscriptionResponse(**data).can_upload is True


async def test_no_subscription_user_can_upload_false():
    # Exhausted bonus, no paid plan: gate says no.
    service = _make_payment_service(subscription=None, can_upload=False)
    data = await service.get_user_subscription("u1")

    assert data["active"] is False
    assert data["can_upload"] is False


async def test_active_subscription_includes_can_upload():
    # Active pool subscription path (the second return dict) carries the field too.
    sub = {
        "tier": "pro",
        "period": "monthly",
        "start_ms": _FUTURE_MS - 30 * 24 * 60 * 60 * 1000,
        "end_ms": _FUTURE_MS,
        "total_credits": 200,
        "credits_remaining": 90,
        "daily_credits": 0,
    }
    service = _make_payment_service(subscription=sub, can_upload=True)
    data = await service.get_user_subscription("u1")

    assert data["active"] is True  # confirms we took the active-subscription path
    assert data["can_upload"] is True


async def test_can_upload_mirrors_the_gate():
    # The field is whatever can_upload_files returns — single source of truth.
    service = _make_payment_service(subscription=None, can_upload=False)
    data = await service.get_user_subscription("u1")
    service.rate_limit_service.can_upload_files.assert_awaited_once_with("u1")
    assert data["can_upload"] is False
