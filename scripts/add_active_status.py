#!/usr/bin/env python3
"""
Backfill legacy sessions by setting their status to active.

This script targets sessions that do not yet have a `status` field, which is the
expected shape for sessions created before the draft/active rollout.

Usage:
    python scripts/backfill_session_status_active.py
    python scripts/backfill_session_status_active.py --apply
    python scripts/backfill_session_status_active.py --apply --limit 100
"""

import argparse
import asyncio
import sys
from pathlib import Path
from typing import Any

project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root / "src"))

from core.config import settings
from db import DBManager
from models.chat_history import SessionStatus


def _build_legacy_sessions_query() -> dict[str, Any]:
    return {"status": {"$exists": False}}


def _build_activation_timestamp(session: dict[str, Any]):
    return session.get("updated_at") or session.get("created_at")


async def backfill_session_statuses(
    *,
    apply_changes: bool = False,
    limit: int | None = None,
) -> None:
    db_manager = DBManager()
    sessions_collection = db_manager.mongo_handler.db[settings.SESSIONS_COLLECTION]

    scanned = 0
    updated = 0
    skipped = 0
    legacy_sessions: list[dict[str, Any]] = []

    try:
        query = _build_legacy_sessions_query()
        total_sessions = await sessions_collection.count_documents({})
        legacy_count = await sessions_collection.count_documents(query)

        print(f"Total sessions: {total_sessions}")
        print(f"Legacy sessions without status: {legacy_count}")
        if limit:
            print(f"Processing limit: {limit}")

        cursor = sessions_collection.find(query).sort("created_at", 1)
        if limit:
            cursor = cursor.limit(limit)

        async for session in cursor:
            legacy_sessions.append(session)

        if not legacy_sessions:
            print("No legacy sessions found. Nothing to backfill.")
            return

        preview_limit = min(20, len(legacy_sessions))
        print(f"Previewing first {preview_limit} legacy sessions:")
        for session in legacy_sessions[:preview_limit]:
            print(
                f"  - {session['_id']} | user={session.get('user_id')} | title={session.get('title')!r}"
            )

        if len(legacy_sessions) > preview_limit:
            print(f"  ... and {len(legacy_sessions) - preview_limit} more")

        if not apply_changes:
            print("Dry run only. Re-run with --apply to write updates.")
            return

        for session in legacy_sessions:
            scanned += 1
            activation_time = _build_activation_timestamp(session)
            if activation_time is None:
                skipped += 1
                continue

            result = await sessions_collection.update_one(
                {"_id": session["_id"], "status": {"$exists": False}},
                {
                    "$set": {
                        "status": SessionStatus.active.value,
                        "activated_at": activation_time,
                    }
                },
            )
            if result.modified_count:
                updated += 1

        print("Backfill summary:")
        print(f"  scanned legacy sessions: {scanned}")
        print(f"  updated sessions: {updated}")
        print(f"  skipped missing timestamps: {skipped}")
    finally:
        db_manager.close_all_connections()


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Set legacy sessions without status to active.",
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Write status=active updates to the sessions collection.",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=None,
        help="Only process the first N legacy sessions.",
    )
    args = parser.parse_args()

    asyncio.run(
        backfill_session_statuses(
            apply_changes=args.apply,
            limit=args.limit,
        )
    )


if __name__ == "__main__":
    main()
