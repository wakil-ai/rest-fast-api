import argparse
import asyncio
import math
import sys
import time
from pathlib import Path

from motor.motor_asyncio import AsyncIOMotorClient
from pymongo.server_api import ServerApi

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.core.config import settings


def _as_int(value, default: int = 0) -> int:
    try:
        if value is None:
            return default
        return int(value)
    except (TypeError, ValueError):
        return default


def _derive_days(start_ms: int, end_ms: int, default: int) -> int:
    if start_ms <= 0 or end_ms <= start_ms:
        return default

    milliseconds_per_day = 24 * 60 * 60 * 1000
    return max(1, math.ceil((end_ms - start_ms) / milliseconds_per_day))


def _normalize_legacy_record(
    user_id: str, legacy_record: dict, *, is_daily: bool
) -> dict:
    now_ms = int(time.time() * 1000)
    daily_credits = _as_int(legacy_record.get("daily_credits"))
    start_ms = _as_int(legacy_record.get("start_ms"))
    end_ms = _as_int(legacy_record.get("end_ms"))
    default_days = 1 if is_daily else 30
    days = _as_int(legacy_record.get("days")) or _derive_days(
        start_ms, end_ms, default_days
    )

    tier = legacy_record.get("tier") or ("daily" if is_daily else None)
    period = legacy_record.get("period") or ("daily" if is_daily else None)

    document = {
        "user_id": user_id,
        "tier": tier,
        "period": period,
        "daily_credits": daily_credits,
        "days": days,
        "total_credits": _as_int(legacy_record.get("total_credits"))
        or daily_credits * days,
        "amount_sum": legacy_record.get("amount_sum"),
        "start_ms": start_ms,
        "end_ms": end_ms,
        "last_order_id": legacy_record.get("last_order_id"),
        "last_transaction_id": legacy_record.get("last_transaction_id"),
        "provider": legacy_record.get("provider"),
        "updated_at_ms": _as_int(legacy_record.get("updated_at_ms")) or now_ms,
        "created_at_ms": _as_int(legacy_record.get("created_at_ms"))
        or _as_int(legacy_record.get("updated_at_ms"))
        or now_ms,
        "migrated_at_ms": now_ms,
        "legacy_source": "users.daily_pass" if is_daily else "users.subscription",
    }

    return document


def _should_replace(existing_record: dict | None, incoming_record: dict) -> bool:
    if not existing_record:
        return True

    existing_end_ms = _as_int(existing_record.get("end_ms"))
    incoming_end_ms = _as_int(incoming_record.get("end_ms"))
    return incoming_end_ms > existing_end_ms


def _invoice_identity_query(provider: str, invoice: dict) -> dict | None:
    order_id = invoice.get("order_id")
    if order_id:
        return {"provider": provider, "order_id": str(order_id)}

    invoice_id = invoice.get("invoice_id")
    if invoice_id:
        return {"provider": provider, "invoice_id": str(invoice_id)}

    return None


async def _migrate_invoice_collection(
    *,
    source_collection,
    target_collection,
    provider: str,
    apply_changes: bool,
) -> tuple[int, int, int]:
    scanned = 0
    migrated = 0
    skipped = 0

    cursor = source_collection.find({})
    async for invoice in cursor:
        scanned += 1
        query = _invoice_identity_query(provider, invoice)
        if not query:
            skipped += 1
            continue

        normalized_invoice = dict(invoice)
        normalized_invoice.pop("_id", None)
        normalized_invoice["provider"] = provider

        if normalized_invoice.get("order_id") is not None:
            normalized_invoice["order_id"] = str(normalized_invoice["order_id"])
        if normalized_invoice.get("invoice_id") is not None:
            normalized_invoice["invoice_id"] = str(normalized_invoice["invoice_id"])

        if not normalized_invoice.get("order_id") and normalized_invoice.get(
            "invoice_id"
        ):
            normalized_invoice["order_id"] = str(normalized_invoice["invoice_id"])

        if normalized_invoice.get("order_id") is None:
            normalized_invoice.pop("order_id", None)
        if normalized_invoice.get("invoice_id") is None:
            normalized_invoice.pop("invoice_id", None)

        existing_invoice = await target_collection.find_one(query)
        if existing_invoice and _as_int(existing_invoice.get("updated_at")) >= _as_int(
            normalized_invoice.get("updated_at")
        ):
            skipped += 1
            continue

        migrated += 1
        if apply_changes:
            await target_collection.update_one(
                query, {"$set": normalized_invoice}, upsert=True
            )

    return scanned, migrated, skipped


async def _cleanup_shared_invoice_null_fields(target_collection) -> None:
    await target_collection.update_many(
        {"order_id": None}, {"$unset": {"order_id": ""}}
    )
    await target_collection.update_many(
        {"invoice_id": None}, {"$unset": {"invoice_id": ""}}
    )


async def _ensure_indexes(db) -> None:
    await db[settings.SUBSCRIPTIONS_COLLECTION].create_index(
        [("user_id", 1)], unique=True
    )
    await db[settings.SUBSCRIPTIONS_COLLECTION].create_index([("end_ms", -1)])
    await db[settings.DAILY_SUBSCRIPTIONS_COLLECTION].create_index(
        [("user_id", 1)], unique=True
    )
    await db[settings.DAILY_SUBSCRIPTIONS_COLLECTION].create_index([("end_ms", -1)])
    payment_invoices = db[settings.PAYMENT_INVOICES_COLLECTION]

    for index_name in ("provider_1_order_id_1", "provider_1_invoice_id_1"):
        try:
            await payment_invoices.drop_index(index_name)
        except Exception:
            pass

    await payment_invoices.create_index(
        [("provider", 1), ("order_id", 1)],
        unique=True,
        partialFilterExpression={"order_id": {"$type": "string"}},
    )
    await payment_invoices.create_index(
        [("provider", 1), ("invoice_id", 1)],
        unique=True,
        partialFilterExpression={"invoice_id": {"$type": "string"}},
    )


async def migrate(
    *, apply_changes: bool, user_id: str | None, limit: int | None
) -> int:
    client = AsyncIOMotorClient(
        settings.MONGODB_URI,
        server_api=ServerApi("1"),
        serverSelectionTimeoutMS=5000,
        tlsAllowInvalidCertificates=True,
    )

    try:
        db = client[settings.MONGODB_DB_NAME]
        users_collection = db[settings.USERS_COLLECTION]
        subscriptions_collection = db[settings.SUBSCRIPTIONS_COLLECTION]
        daily_subscriptions_collection = db[settings.DAILY_SUBSCRIPTIONS_COLLECTION]
        payment_invoices_collection = db[settings.PAYMENT_INVOICES_COLLECTION]
        payme_invoices_collection = db[settings.PAYME_INVOICES_COLLECTION]
        click_invoices_collection = db[settings.CLICK_INVOICES_COLLECTION]

        await client.admin.command("ping")
        if apply_changes:
            await _cleanup_shared_invoice_null_fields(payment_invoices_collection)
        await _ensure_indexes(db)

        query: dict = {
            "$or": [
                {"subscription": {"$type": "object"}},
                {"daily_pass": {"$type": "object"}},
            ]
        }
        if user_id:
            query["$and"] = [
                {"$or": [{"user_id": user_id}, {"_id": user_id}]},
            ]

        cursor = users_collection.find(query).sort("_id", 1)

        if limit is not None:
            cursor = cursor.limit(limit)

        scanned = 0
        migrated_subscriptions = 0
        migrated_daily_subscriptions = 0
        cleared_legacy_fields = 0
        skipped_existing = 0

        async for user in cursor:
            scanned += 1
            canonical_user_id = str(user.get("user_id") or user.get("_id"))
            unset_fields: dict[str, str] = {}

            legacy_subscription = user.get("subscription")
            if isinstance(legacy_subscription, dict):
                normalized_subscription = _normalize_legacy_record(
                    canonical_user_id,
                    legacy_subscription,
                    is_daily=False,
                )
                existing_subscription = await subscriptions_collection.find_one(
                    {"user_id": canonical_user_id}
                )

                if _should_replace(existing_subscription, normalized_subscription):
                    migrated_subscriptions += 1
                    if apply_changes:
                        await subscriptions_collection.update_one(
                            {"user_id": canonical_user_id},
                            {"$set": normalized_subscription},
                            upsert=True,
                        )
                else:
                    skipped_existing += 1

                unset_fields["subscription"] = ""

            legacy_daily_subscription = user.get("daily_pass")
            if isinstance(legacy_daily_subscription, dict):
                normalized_daily_subscription = _normalize_legacy_record(
                    canonical_user_id,
                    legacy_daily_subscription,
                    is_daily=True,
                )
                existing_daily_subscription = (
                    await daily_subscriptions_collection.find_one(
                        {"user_id": canonical_user_id}
                    )
                )

                if _should_replace(
                    existing_daily_subscription, normalized_daily_subscription
                ):
                    migrated_daily_subscriptions += 1
                    if apply_changes:
                        await daily_subscriptions_collection.update_one(
                            {"user_id": canonical_user_id},
                            {"$set": normalized_daily_subscription},
                            upsert=True,
                        )
                else:
                    skipped_existing += 1

                unset_fields["daily_pass"] = ""

            if apply_changes and unset_fields:
                result = await users_collection.update_one(
                    {"_id": user["_id"]}, {"$unset": unset_fields}
                )
                if result.modified_count:
                    cleared_legacy_fields += 1

        mode = "apply" if apply_changes else "dry-run"
        print(f"Migration mode: {mode}")
        print(f"Users scanned: {scanned}")
        print(f"Subscriptions to migrate: {migrated_subscriptions}")
        print(f"Daily subscriptions to migrate: {migrated_daily_subscriptions}")
        print(f"Existing newer records skipped: {skipped_existing}")
        if apply_changes:
            print(f"Users cleaned from legacy fields: {cleared_legacy_fields}")
        else:
            print("No data was written. Re-run with --apply to perform the migration.")

        payme_scanned, payme_migrated, payme_skipped = (
            await _migrate_invoice_collection(
                source_collection=payme_invoices_collection,
                target_collection=payment_invoices_collection,
                provider="payme",
                apply_changes=apply_changes,
            )
        )
        click_scanned, click_migrated, click_skipped = (
            await _migrate_invoice_collection(
                source_collection=click_invoices_collection,
                target_collection=payment_invoices_collection,
                provider="click",
                apply_changes=apply_changes,
            )
        )

        print(f"Payme invoices scanned: {payme_scanned}")
        print(f"Payme invoices migrated: {payme_migrated}")
        print(f"Payme invoices skipped: {payme_skipped}")
        print(f"Click invoices scanned: {click_scanned}")
        print(f"Click invoices migrated: {click_migrated}")
        print(f"Click invoices skipped: {click_skipped}")

        return 0
    finally:
        client.close()


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Migrate legacy payment data into the new subscription and invoice collections."
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Write migrated data and remove legacy subscription fields from users.",
    )
    parser.add_argument(
        "--user-id",
        help="Only migrate a single user_id/_id.",
    )
    parser.add_argument(
        "--limit",
        type=int,
        help="Only inspect the first N matching users.",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    return asyncio.run(
        migrate(apply_changes=args.apply, user_id=args.user_id, limit=args.limit)
    )


if __name__ == "__main__":
    raise SystemExit(main())
