import re
from datetime import datetime, timezone
from urllib.parse import urlparse

from pymongo import ReturnDocument

from src.core.config import settings
from src.core.dependencies import get_mongo_handler
from src.core.logger import logger


class ReferralService:
    """Track website sources that send users to WakilAI."""

    COLLECTION_NAME = settings.REFERRAL_SOURCES_COLLECTION
    HOSTNAME_PATTERN = re.compile(
        r"^(?=.{1,253}$)"
        r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?"
        r"(?:\.[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?)*$"
    )

    def __init__(self):
        self.mongo_handler = get_mongo_handler()

    def normalize_source(self, source: str) -> str:
        source = source.strip().lower()
        if not source:
            raise ValueError("Source is required")

        parsed = urlparse(source if "://" in source else f"//{source}")
        hostname = parsed.hostname or source.split("/", 1)[0].split("?", 1)[0]
        hostname = hostname.removeprefix("www.").strip(".")

        if not self.HOSTNAME_PATTERN.fullmatch(hostname):
            raise ValueError("Source must be a valid website hostname")

        return hostname

    async def track_source(self, source: str) -> dict:
        normalized_source = self.normalize_source(source)
        now = datetime.now(timezone.utc)

        collection = self.mongo_handler.db[self.COLLECTION_NAME]
        document = await collection.find_one_and_update(
            {"source": normalized_source},
            {
                "$inc": {"total_count": 1},
                "$set": {"last_seen_at": now},
                "$setOnInsert": {
                    "source": normalized_source,
                    "first_seen_at": now,
                },
            },
            upsert=True,
            return_document=ReturnDocument.AFTER,
        )

        logger.info(f"[ReferralService] Tracked referral source '{normalized_source}'")
        return document

    async def get_stats(self, limit: int = 100) -> list[dict]:
        collection = self.mongo_handler.db[self.COLLECTION_NAME]
        cursor = collection.find({}).sort("total_count", -1).limit(limit)
        return await cursor.to_list(length=limit)
