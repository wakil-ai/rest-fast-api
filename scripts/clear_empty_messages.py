#!/usr/bin/env python3
"""
Delete chat sessions that do not have any messages.

Usage:
    python scripts/cleanup_empty_sessions.py
    python scripts/cleanup_empty_sessions.py --apply
    python scripts/cleanup_empty_sessions.py --apply --limit 100
    python scripts/cleanup_empty_sessions.py --apply --user-id user-123
"""

import argparse
import asyncio
import sys
from pathlib import Path
from typing import Any

project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

from app.core.config import settings
from app.db import DBManager


def _build_empty_sessions_pipeline(
    *,
    messages_collection_name: str,
    user_id: str | None = None,
    limit: int | None = None,
) -> list[dict[str, Any]]:
    pipeline: list[dict[str, Any]] = []

    if user_id:
        pipeline.append({"$match": {"user_id": user_id}})

    pipeline.extend(
        [
            {
                "$lookup": {
                    "from": messages_collection_name,
                    "let": {"session_id": "$_id"},
                    "pipeline": [
                        {
                            "$match": {
                                "$expr": {"$eq": ["$session_id", "$$session_id"]}
                            }
                        },
                        {"$limit": 1},
                        {"$project": {"_id": 1}},
                    ],
                    "as": "linked_messages",
                }
            },
            {"$match": {"linked_messages.0": {"$exists": False}}},
            {
                "$project": {
                    "_id": 1,
                    "user_id": 1,
                    "title": 1,
                    "created_at": 1,
                    "updated_at": 1,
                }
            },
            {"$sort": {"created_at": 1}},
        ]
    )

    if limit:
        pipeline.append({"$limit": limit})

    return pipeline


async def cleanup_empty_sessions(
    *,
    apply_changes: bool = False,
    user_id: str | None = None,
    limit: int | None = None,
) -> None:
    db_manager = DBManager()
    sessions_collection = db_manager.mongo_handler.db[settings.SESSIONS_COLLECTION]
    messages_collection = db_manager.mongo_handler.db[settings.MESSAGES_COLLECTION]

    inspected = 0
    deleted_sessions = 0
    skipped_became_non_empty = 0
    empty_sessions: list[dict[str, Any]] = []

    try:
        base_filter = {"user_id": user_id} if user_id else {}
        total_sessions = await sessions_collection.count_documents(base_filter)

        print(f"Total sessions in scope: {total_sessions}")
        if user_id:
            print(f"Filtered user_id: {user_id}")
        if limit:
            print(f"Processing limit: {limit}")

        pipeline = _build_empty_sessions_pipeline(
            messages_collection_name=settings.MESSAGES_COLLECTION,
            user_id=user_id,
            limit=limit,
        )

        async for session in sessions_collection.aggregate(pipeline):
            empty_sessions.append(session)

        print(f"Empty sessions found: {len(empty_sessions)}")

        if not empty_sessions:
            print("No empty sessions found. Nothing to clean up.")
            return

        preview_limit = min(20, len(empty_sessions))
        print(f"Previewing first {preview_limit} empty sessions:")
        for session in empty_sessions[:preview_limit]:
            print(
                f"  - {session['_id']} | user={session.get('user_id')} | title={session.get('title')!r}"
            )

        if len(empty_sessions) > preview_limit:
            remaining = len(empty_sessions) - preview_limit
            print(f"  ... and {remaining} more")

        if not apply_changes:
            print("Dry run only. Re-run with --apply to delete these sessions.")
            return

        for session in empty_sessions:
            inspected += 1
            session_id = session["_id"]

            existing_message = await messages_collection.find_one(
                {"session_id": session_id},
                {"_id": 1},
            )
            if existing_message:
                skipped_became_non_empty += 1
                continue

            session_delete_result = await sessions_collection.delete_one({"_id": session_id})
            if session_delete_result.deleted_count == 0:
                continue

            deleted_sessions += 1

        print("Cleanup summary:")
        print(f"  inspected empty sessions: {inspected}")
        print(f"  deleted sessions: {deleted_sessions}")
        print(f"  skipped because messages appeared: {skipped_became_non_empty}")
    finally:
        db_manager.close_all_connections()


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Delete chat sessions that do not have any messages.",
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Delete the matched empty sessions. Without this flag the script runs as a dry run.",
    )
    parser.add_argument(
        "--user-id",
        type=str,
        default=None,
        help="Only inspect sessions for a specific user.",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=None,
        help="Only inspect the first N empty sessions.",
    )
    args = parser.parse_args()

    asyncio.run(
        cleanup_empty_sessions(
            apply_changes=args.apply,
            user_id=args.user_id,
            limit=args.limit,
        )
    )


if __name__ == "__main__":
    main()