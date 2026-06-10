#!/usr/bin/env python3
"""
Sanitize duplicate phone numbers in the users collection and enforce uniqueness.

For every phone number shared by more than one user the script keeps a single
"winner" record and clears `phone_number` (sets it to null) on the remaining
records. Those users will be prompted to enter a new number on their next login.

The winner is chosen deterministically by, in order of priority:
  1. activity score (sessions + messages + files + subscriptions) - desc
  2. updated_at - desc
  3. created_at - desc
  4. _id - asc
Activity is weighed first so a real, used account is never dropped in favour of
an empty duplicate. Pass --no-activity-score to rank purely by recency.

After cleanup (with --apply) the script creates a UNIQUE partial index on
`phone_number` so future duplicates are rejected at the database level. The
index is partial (only string values) so the many null `phone_number` documents
do not collide with each other.

Usage:
    python scripts/dedupe_phone_numbers.py                 # dry run, no writes
    python scripts/dedupe_phone_numbers.py --apply          # clean + build index
    python scripts/dedupe_phone_numbers.py --apply --index-only
    python scripts/dedupe_phone_numbers.py --apply --no-index
"""

import argparse
import asyncio
import json

import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root / "src"))

from motor.motor_asyncio import (
    AsyncIOMotorCollection,
    AsyncIOMotorDatabase,
)

from core.config import settings
from db import DBManager

PHONE_INDEX_NAME = "uniq_phone_number"

# Collections used to compute an account's "activity score".
ACTIVITY_COLLECTIONS = [
    settings.SESSIONS_COLLECTION,
    settings.MESSAGES_COLLECTION,
    settings.FILES_COLLECTION,
    settings.SUBSCRIPTIONS_COLLECTION,
]


def _mask(phone: Any) -> str:
    """Mask a phone number for safe logging."""
    if not phone:
        return repr(phone)
    phone = str(phone)
    if len(phone) <= 7:
        return phone[:2] + "***"
    return f"{phone[:5]}{'*' * (len(phone) - 7)}{phone[-2:]}"


def _min_datetime() -> datetime:
    return datetime.min.replace(tzinfo=timezone.utc)


def _as_aware(value: Any) -> datetime:
    """Coerce a stored timestamp into a comparable timezone-aware datetime."""
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    return _min_datetime()


async def _activity_score(
    db: AsyncIOMotorDatabase[dict[str, Any]], user_id: Any
) -> int:
    """Count related records across activity collections for a user."""
    total = 0
    for collection_name in ACTIVITY_COLLECTIONS:
        total += await db[collection_name].count_documents({"user_id": user_id})
    return total


async def _find_duplicate_groups(
    users: AsyncIOMotorCollection[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Return phone_number groups (non-null, non-empty) with more than one user."""
    pipeline = [
        {"$match": {"phone_number": {"$nin": [None, ""]}}},
        {
            "$group": {
                "_id": "$phone_number",
                "count": {"$sum": 1},
                "users": {
                    "$push": {
                        "_id": "$_id",
                        "user_id": "$user_id",
                        "updated_at": "$updated_at",
                        "created_at": "$created_at",
                    }
                },
            }
        },
        {"$match": {"count": {"$gt": 1}}},
        {"$sort": {"count": -1}},
    ]
    return [group async for group in users.aggregate(pipeline)]


async def _rank_users(
    db: AsyncIOMotorDatabase[dict[str, Any]],
    members: list[dict[str, Any]],
    use_activity: bool,
) -> list[dict[str, Any]]:
    """Sort group members best-first; the first element is the keep winner."""
    ranked: list[dict[str, Any]] = []
    for member in members:
        score = await _activity_score(db, member["_id"]) if use_activity else 0
        ranked.append(
            {
                **member,
                "activity": score,
                "_updated": _as_aware(member.get("updated_at")),
                "_created": _as_aware(member.get("created_at")),
            }
        )

    # Stable sort, least-significant key first so directions can differ:
    # final tiebreak _id ascending, then activity/recency descending wins.
    ranked.sort(key=lambda m: str(m["_id"]))
    ranked.sort(
        key=lambda m: (m["activity"], m["_updated"], m["_created"]),
        reverse=True,
    )
    return ranked


def _write_backup(losers: list[dict[str, Any]]) -> Path | None:
    """Persist the records we are about to clear, for rollback."""
    if not losers:
        return None
    backup_dir = project_root / "scripts" / "backups"
    backup_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    backup_path = backup_dir / f"phone_dedupe_{stamp}.json"
    backup_path.write_text(json.dumps(losers, indent=2, default=str))
    return backup_path


async def dedupe_phone_numbers(
    *,
    apply_changes: bool,
    use_activity: bool,
    invalidate_cache: bool,
) -> bool:
    """Clear phone_number on duplicate losers. Returns True if no duplicates remain."""
    db_manager = DBManager()
    db = db_manager.mongo_handler.db
    users = db[settings.USERS_COLLECTION]

    total = await users.count_documents({})
    with_phone = await users.count_documents({"phone_number": {"$nin": [None, ""]}})
    print(
        f"Database: {settings.MONGODB_DB_NAME} | collection: {settings.USERS_COLLECTION}"
    )
    print(f"Total users: {total} | with phone_number: {with_phone}")

    groups = await _find_duplicate_groups(users)
    if not groups:
        print("No duplicate phone numbers found. Nothing to clean.")
        return True

    excess = sum(g["count"] - 1 for g in groups)
    print(f"Duplicate phone numbers: {len(groups)} | records to clear: {excess}")
    if use_activity:
        print("Winner selection: activity score, then most recent updated_at.")
    else:
        print("Winner selection: most recent updated_at (activity scoring disabled).")

    losers: list[dict[str, Any]] = []
    for group in groups:
        ranked = await _rank_users(db, group["users"], use_activity)
        winner, dropped = ranked[0], ranked[1:]
        print(
            f"\n  phone {_mask(group['_id'])} ({group['count']} users)\n"
            f"    keep   {winner['_id']} "
            f"(activity={winner['activity']}, updated={winner['_updated']})"
        )
        for member in dropped:
            print(
                f"    clear  {member['_id']} "
                f"(activity={member['activity']}, updated={member['_updated']})"
            )
            losers.append(
                {
                    "_id": member["_id"],
                    "user_id": member.get("user_id"),
                    "phone_number": group["_id"],
                }
            )

    if not apply_changes:
        print("\nDry run only. Re-run with --apply to write changes.")
        return False

    backup_path = _write_backup(losers)
    if backup_path:
        print(f"\nBackup written: {backup_path}")

    now = datetime.now(timezone.utc)
    cleared = 0
    for loser in losers:
        result = await users.update_one(
            {"_id": loser["_id"], "phone_number": loser["phone_number"]},
            {"$set": {"phone_number": None, "updated_at": now}},
        )
        cleared += result.modified_count

    print(f"Cleared phone_number on {cleared} of {len(losers)} records.")

    if invalidate_cache:
        _invalidate_user_caches(losers)

    remaining = await _find_duplicate_groups(users)
    if remaining:
        print(
            f"WARNING: {len(remaining)} duplicate group(s) still present after cleanup."
        )
        return False
    print("All duplicates resolved.")
    return True


def _invalidate_user_caches(losers: list[dict[str, Any]]) -> None:
    """Best-effort Redis cache invalidation for the cleared users."""
    try:
        from services.redis_service import RedisService

        redis_service = RedisService()
    except Exception as exc:
        print(f"Skipped cache invalidation (Redis unavailable): {exc}")
        return

    invalidated = 0
    for loser in losers:
        for key_id in {loser["_id"], loser.get("user_id")}:
            if key_id:
                redis_service.invalidate_cache(f"user:{key_id}")
                invalidated += 1
    print(f"Invalidated {invalidated} Redis user cache key(s).")


async def create_unique_index(*, apply_changes: bool) -> None:
    """Create a UNIQUE partial index on phone_number (string values only)."""
    db_manager = DBManager()
    users = db_manager.mongo_handler.db[settings.USERS_COLLECTION]

    existing = await users.index_information()
    if PHONE_INDEX_NAME in existing:
        print(f"Index '{PHONE_INDEX_NAME}' already exists. Skipping.")
        return

    # Guard: never try to build a unique index while duplicates remain.
    groups = await _find_duplicate_groups(users)
    if groups:
        print(
            f"Refusing to build unique index: {len(groups)} duplicate group(s) remain. "
            "Run the cleanup (--apply without --index-only) first."
        )
        return

    if not apply_changes:
        print(
            f"Dry run: would create UNIQUE partial index '{PHONE_INDEX_NAME}' "
            "on phone_number (string values only)."
        )
        return

    await users.create_index(
        "phone_number",
        name=PHONE_INDEX_NAME,
        unique=True,
        partialFilterExpression={"phone_number": {"$type": "string"}},
    )
    print(f"Created UNIQUE partial index '{PHONE_INDEX_NAME}' on phone_number.")


async def run(args: argparse.Namespace) -> None:
    db_manager = DBManager()
    try:
        clean_ok = True
        if not args.index_only:
            clean_ok = await dedupe_phone_numbers(
                apply_changes=args.apply,
                use_activity=not args.no_activity_score,
                invalidate_cache=not args.no_cache_invalidation,
            )

        if not args.no_index and (args.index_only or clean_ok):
            print()
            await create_unique_index(apply_changes=args.apply)
    finally:
        await db_manager.mongo_handler.close_connection()


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Remove duplicate phone numbers and enforce uniqueness.",
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Write changes. Without it the script only reports (dry run).",
    )
    parser.add_argument(
        "--index-only",
        action="store_true",
        help="Skip cleanup and only create the unique index.",
    )
    parser.add_argument(
        "--no-index",
        action="store_true",
        help="Skip creating the unique index after cleanup.",
    )
    parser.add_argument(
        "--no-activity-score",
        action="store_true",
        help="Rank winners by recency only, ignoring related-record activity.",
    )
    parser.add_argument(
        "--no-cache-invalidation",
        action="store_true",
        help="Do not invalidate Redis user caches after clearing phone numbers.",
    )
    args = parser.parse_args()

    asyncio.run(run(args))


if __name__ == "__main__":
    main()
