#!/usr/bin/env python3
"""
Migration script to add web_client field to all users in MongoDB.

This script backfills web_client='wakilai' for legacy users that do not have a
web_client set yet.
This maintains backward compatibility by tagging legacy users as 'wakilai' (the main web client).

Usage:
    python scripts/migrate_web_client.py

The script will:
1. Connect to MongoDB using settings from .env
2. Find all users without web_client field
3. Update them with web_client='wakilai'
4. Display statistics about the migration
"""

import asyncio
import sys
from pathlib import Path

# Add project root to path
project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

from app.core.config import settings
from app.core.logger import logger
from app.db import DBManager


async def migrate_web_client():
    """Migrate all users to add web_client field."""
    db_manager = None

    try:
        # Initialize database manager
        db_manager = DBManager()
        users_collection = db_manager.mongo_handler.db[settings.USERS_COLLECTION]
        migration_filter = {
            "$or": [
                {"web_client": {"$exists": False}},
                {"web_client": None},
                {"web_client": ""},
            ]
        }

        logger.info("=" * 80)
        logger.info("Starting web_client migration...")
        logger.info("=" * 80)

        # Count users without web_client field
        users_without_web_client = await users_collection.count_documents(
            migration_filter
        )

        logger.info(f"Found {users_without_web_client} users without web_client field")

        if users_without_web_client == 0:
            logger.info(
                "✓ All users already have web_client field. Migration not needed."
            )
            return

        # Backfill only legacy users that are still missing web_client.
        result = await users_collection.update_many(
            migration_filter,
            {"$set": {"web_client": settings.WAKILAI_WEB_CLIENT_NAME}},
        )

        logger.info("✓ Migration completed successfully!")
        logger.info(f"  - Matched: {result.matched_count}")
        logger.info(f"  - Modified: {result.modified_count}")

        # Verify migration
        total_users = await users_collection.count_documents({})
        users_with_web_client = await users_collection.count_documents(
            {"web_client": {"$exists": True}}
        )

        logger.info("\nVerification:")
        logger.info(f"  - Total users: {total_users}")
        logger.info(f"  - Users with web_client: {users_with_web_client}")
        logger.info(f"  - Missing web_client: {total_users - users_with_web_client}")

        # Count by web_client type
        wakilai_count = await users_collection.count_documents(
            {"web_client": settings.WAKILAI_WEB_CLIENT_NAME}
        )
        birdarcha_count = await users_collection.count_documents(
            {"web_client": settings.DT_WEB_CLIENT_NAME}
        )

        logger.info("\nWeb client distribution:")
        logger.info(f"  - wakilai: {wakilai_count}")
        logger.info(f"  - birdarcha: {birdarcha_count}")

        if total_users == users_with_web_client:
            logger.info("\n✓ Migration verified successfully!")
        else:
            logger.warning(
                f"\n✗ Warning: {total_users - users_with_web_client} users still missing web_client"
            )

        logger.info("=" * 80)

    except Exception as e:
        logger.error(f"✗ Migration failed: {str(e)}", exc_info=True)
        raise
    finally:
        # Close database connection
        if db_manager is not None:
            await db_manager.close_all_connections()


async def main():
    """Main entry point."""
    try:
        await migrate_web_client()
        logger.info("\n✓ Script completed successfully")
        sys.exit(0)
    except Exception as e:
        logger.error(f"\n✗ Script failed: {str(e)}")
        sys.exit(1)


if __name__ == "__main__":
    asyncio.run(main())
