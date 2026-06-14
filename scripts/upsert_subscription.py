#!/usr/bin/env python3
"""
Create or upsert a subscription for a user.

Daily subscriptions create one expiring credit lot per order. Monthly/yearly
subscriptions use the regular subscription upsert path.

Usage:
    python scripts/upsert_subscription.py
    python scripts/upsert_subscription.py --user-id user_123 --tier basic --period daily --date "2026-06-13 15:30" --apply
    python scripts/upsert_subscription.py --user-id user_123 --tier standard --period monthly --date "now" --order-id manual-standard-001 --apply
"""

import argparse
import asyncio
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

from app.core.config import settings
from app.core.dependencies import get_mongo_handler
from app.services.payments.base import BasePaymentService
from app.services.subscription_storage import SubscriptionStorage


LOCAL_TZ = datetime.now().astimezone().tzinfo


def _prompt_if_missing(value: str | None, prompt: str, default: str | None = None) -> str:
    if value:
        return value

    suffix = f" [{default}]" if default else ""
    entered = input(f"{prompt}{suffix}: ").strip()
    if entered:
        return entered
    if default is not None:
        return default
    raise ValueError(f"{prompt} is required")


def _parse_datetime_to_ms(value: str) -> int:
    raw = value.strip()
    if raw.lower() == "now":
        return int(time.time() * 1000)

    normalized = raw.replace("T", " ")
    formats = [
        "%Y-%m-%d %H:%M:%S",
        "%Y-%m-%d %H:%M",
        "%Y-%m-%d",
    ]
    parsed = None
    for fmt in formats:
        try:
            parsed = datetime.strptime(normalized, fmt)
            break
        except ValueError:
            continue

    if parsed is None:
        try:
            parsed = datetime.fromisoformat(raw)
        except ValueError as exc:
            raise ValueError(
                "Date must be 'now', 'YYYY-MM-DD', 'YYYY-MM-DD HH:MM', "
                "'YYYY-MM-DD HH:MM:SS', or ISO-8601."
            ) from exc

    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=LOCAL_TZ)
    return int(parsed.astimezone(timezone.utc).timestamp() * 1000)


def _format_ms(ms: int) -> str:
    return datetime.fromtimestamp(ms / 1000, tz=timezone.utc).isoformat()


async def upsert_subscription(
    *,
    user_id: str,
    tier: str,
    period: str,
    start_ms: int,
    order_id: str | None,
    provider: str,
    transaction_id: str | None,
    apply_changes: bool,
) -> None:
    payment_service = BasePaymentService()
    quote = payment_service._get_subscription_quote(tier, period)

    final_order_id = order_id or f"manual-{user_id}-{tier}-{period}-{start_ms}"

    print("Subscription write preview:")
    print(f"  user_id: {user_id}")
    print(f"  tier/period: {tier}/{period}")
    print(f"  start_ms: {start_ms} ({_format_ms(start_ms)})")
    print(f"  order_id: {final_order_id}")
    print(f"  provider: {provider}")
    print(f"  daily_credits: {quote['daily_credits']}")
    print(f"  total_credits: {quote['total_credits']}")
    print(f"  amount_sum: {quote['amount_sum']}")

    if not apply_changes:
        print("Dry run only. Re-run with --apply to write the subscription.")
        return

    storage = SubscriptionStorage()
    try:
        document = await storage.upsert_subscription(
            user_id=user_id,
            quote=quote,
            order_id=final_order_id,
            transaction_id=transaction_id,
            now_ms=start_ms,
            provider=provider,
        )
        print("Subscription written:")
        print(f"  credits_remaining: {document.get('credits_remaining')}")
        print(f"  start_ms: {document.get('start_ms')} ({_format_ms(document['start_ms'])})")
        print(f"  end_ms: {document.get('end_ms')} ({_format_ms(document['end_ms'])})")
        print(f"  stored_order_id: {document.get('order_id') or document.get('last_order_id')}")
    finally:
        mongo_handler = get_mongo_handler()
        await mongo_handler.close_connection()


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Manually create/upsert a user subscription.",
    )
    parser.add_argument("--user-id", default=None, help="Target user id.")
    parser.add_argument(
        "--tier",
        choices=["basic", "standard", "premium", "pro", "test"],
        default=None,
        help="Subscription tier.",
    )
    parser.add_argument(
        "--period",
        choices=["daily", "monthly", "yearly"],
        default=None,
        help="Subscription period.",
    )
    parser.add_argument(
        "--date",
        default=None,
        help="Start date/time: now, YYYY-MM-DD, YYYY-MM-DD HH:MM, or ISO-8601.",
    )
    parser.add_argument(
        "--order-id",
        default=None,
        help="Idempotency key. Reuse returns the existing daily lot.",
    )
    parser.add_argument(
        "--provider",
        default="manual",
        help="Provider label stored on the subscription document.",
    )
    parser.add_argument("--transaction-id", default=None)
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Actually write to MongoDB. Without this flag, only previews.",
    )
    args = parser.parse_args()

    user_id = _prompt_if_missing(args.user_id, "User ID")
    tier = _prompt_if_missing(args.tier, "Tier", "basic")
    period = _prompt_if_missing(args.period, "Period", "daily")
    date_value = _prompt_if_missing(args.date, "Start date/time", "now")

    start_ms = _parse_datetime_to_ms(date_value)

    asyncio.run(
        upsert_subscription(
            user_id=user_id,
            tier=tier,
            period=period,
            start_ms=start_ms,
            order_id=args.order_id,
            provider=args.provider or settings.APP_NAME,
            transaction_id=args.transaction_id,
            apply_changes=args.apply,
        )
    )


if __name__ == "__main__":
    main()
