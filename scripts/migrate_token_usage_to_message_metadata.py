#!/usr/bin/env python3
"""
Migrate legacy token usage documents into message metadata.

Usage:
    python scripts/migrate_token_usage_to_message_metadata.py
    python scripts/migrate_token_usage_to_message_metadata.py --apply
    python scripts/migrate_token_usage_to_message_metadata.py --apply --delete-legacy
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


def _build_update_fields(token_doc: dict[str, Any]) -> dict[str, Any]:
    update_fields: dict[str, Any] = {}

    if model := token_doc.get("model"):
        update_fields["metadata.model"] = model

    token_usage_fields = {
        "input_token": token_doc.get("input_token"),
        "context_token": token_doc.get("context_token"),
        "output_token": token_doc.get("output_token"),
        "embedding_input_token": token_doc.get("embedding_input_token"),
    }

    for key, value in token_usage_fields.items():
        if value is not None:
            update_fields[f"metadata.token_usage.{key}"] = int(value)

    return update_fields


async def migrate(
    *,
    apply_changes: bool = False,
    delete_legacy: bool = False,
    limit: int | None = None,
) -> None:
    db_manager = DBManager()
    token_collection = db_manager.mongo_handler.db[settings.TOKEN_COUNTING_COLLECTION]
    messages_collection = db_manager.mongo_handler.db[settings.MESSAGES_COLLECTION]

    scanned = 0
    matched = 0
    updated = 0
    skipped = 0
    missing_messages = 0
    migrated_ids: list[Any] = []

    try:
        total_documents = await token_collection.count_documents({})
        print(f"Legacy token usage documents: {total_documents}")
        if limit:
            print(f"Processing limit: {limit}")

        cursor = token_collection.find({}).sort("created_at", 1)
        if limit:
            cursor = cursor.limit(limit)

        async for token_doc in cursor:
            scanned += 1
            message_id = token_doc.get("message_id") or token_doc.get("_id")
            if not message_id:
                skipped += 1
                continue

            update_fields = _build_update_fields(token_doc)
            if not update_fields:
                skipped += 1
                continue

            message = await messages_collection.find_one({"_id": message_id})
            if not message:
                missing_messages += 1
                continue

            matched += 1
            if not apply_changes:
                continue

            result = await messages_collection.update_one(
                {"_id": message_id},
                {"$set": update_fields},
            )
            if result.matched_count:
                updated += 1
                migrated_ids.append(token_doc.get("_id"))

        print("Migration summary:")
        print(f"  scanned: {scanned}")
        print(f"  matched messages: {matched}")
        print(f"  updated messages: {updated}")
        print(f"  skipped token docs: {skipped}")
        print(f"  missing messages: {missing_messages}")

        if not apply_changes:
            print("Dry run only. Re-run with --apply to write updates.")
            return

        if delete_legacy and migrated_ids:
            delete_result = await token_collection.delete_many(
                {"_id": {"$in": migrated_ids}}
            )
            print(f"Deleted legacy token usage docs: {delete_result.deleted_count}")
        elif delete_legacy:
            print("No migrated legacy documents to delete.")
    finally:
        db_manager.close_all_connections()


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Migrate legacy token usage records into message metadata.",
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Write metadata updates to the messages collection.",
    )
    parser.add_argument(
        "--delete-legacy",
        action="store_true",
        help="Delete migrated legacy token usage documents after updating messages.",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=None,
        help="Only process the first N token usage documents.",
    )
    args = parser.parse_args()

    asyncio.run(
        migrate(
            apply_changes=args.apply,
            delete_legacy=args.delete_legacy,
            limit=args.limit,
        )
    )


if __name__ == "__main__":
    main()
