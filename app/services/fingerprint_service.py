from datetime import datetime, timezone
from typing import Any

from pymongo import ReturnDocument

from app.core.config import settings
from app.core.dependencies import get_db_manager, get_mongo_handler
from app.core.logger import logger
from app.utils.user_management import clean_for_mongodb


class FingerprintService:
    """Track device fingerprints and detect multiple accounts per device."""

    COLLECTION_NAME = settings.FINGERPRINTS_COLLECTION
    MAX_TRACKED_IPS = 10
    MAX_TRACKED_USER_AGENTS = 5

    def __init__(self):
        self.mongo_handler = get_mongo_handler()
        self.db_manager = get_db_manager()
        self._indexes_ready = False

    async def _ensure_indexes(self) -> None:
        if self._indexes_ready:
            return
        try:
            await self.db_manager.create_collection(self.COLLECTION_NAME)
            collection = self.mongo_handler.db[self.COLLECTION_NAME]
            await collection.create_index(
                [("visitor_id", 1), ("user_id", 1)],
                unique=True,
                name="visitor_user_unique",
            )
            await collection.create_index("visitor_id")
            await collection.create_index("user_id")
            await collection.create_index([("last_seen_at", -1)])
            self._indexes_ready = True
        except Exception as exc:
            logger.warning(f"[FingerprintService] Index setup: {exc}")

    async def collect(
        self,
        *,
        user_id: str,
        visitor_id: str,
        metadata: dict[str, Any] | None = None,
        ip_address: str | None = None,
        user_agent: str | None = None,
    ) -> dict[str, Any]:
        await self._ensure_indexes()
        now = datetime.now(timezone.utc)
        visitor_id = visitor_id.strip()
        user_id = user_id.strip()

        update: dict[str, Any] = {
            "$set": {
                "visitor_id": visitor_id,
                "user_id": user_id,
                "last_seen_at": now,
                "metadata": clean_for_mongodb(metadata or {}),
            },
            "$inc": {"access_count": 1},
            "$setOnInsert": {
                "first_seen_at": now,
                "is_blocked": False,
            },
        }

        if ip_address:
            update["$addToSet"] = {"ip_addresses": ip_address}
        if user_agent:
            ua_key = "user_agents"
            update.setdefault("$addToSet", {})[ua_key] = user_agent

        collection = self.mongo_handler.db[self.COLLECTION_NAME]
        document = await collection.find_one_and_update(
            {"visitor_id": visitor_id, "user_id": user_id},
            update,
            upsert=True,
            return_document=ReturnDocument.AFTER,
        )

        if document:
            await self._trim_list_fields(collection, document["_id"])

        account_count = await self._count_users_for_visitor(visitor_id)
        is_blocked = bool(document and document.get("is_blocked"))

        return {
            "visitor_id": visitor_id,
            "user_id": user_id,
            "access_count": int((document or {}).get("access_count", 1)),
            "account_count_on_device": account_count,
            "is_blocked": is_blocked,
            "is_multi_account": account_count > 1,
        }

    async def _trim_list_fields(self, collection, doc_id) -> None:
        doc = await collection.find_one(
            {"_id": doc_id},
            projection={"ip_addresses": 1, "user_agents": 1},
        )
        if not doc:
            return

        trim: dict[str, Any] = {}
        ips = doc.get("ip_addresses") or []
        if len(ips) > self.MAX_TRACKED_IPS:
            trim["ip_addresses"] = ips[-self.MAX_TRACKED_IPS :]
        agents = doc.get("user_agents") or []
        if len(agents) > self.MAX_TRACKED_USER_AGENTS:
            trim["user_agents"] = agents[-self.MAX_TRACKED_USER_AGENTS :]

        if trim:
            await collection.update_one({"_id": doc_id}, {"$set": trim})

    async def _count_users_for_visitor(self, visitor_id: str) -> int:
        collection = self.mongo_handler.db[self.COLLECTION_NAME]
        result = await collection.aggregate(
            [
                {"$match": {"visitor_id": visitor_id}},
                {"$group": {"_id": None, "count": {"$sum": 1}}},
            ]
        ).to_list(length=1)
        return int(result[0]["count"]) if result else 0

    async def list_multi_account(
        self,
        *,
        min_accounts: int = 2,
        limit: int = 50,
        skip: int = 0,
        include_blocked: bool = True,
    ) -> tuple[list[dict[str, Any]], int]:
        await self._ensure_indexes()
        collection = self.mongo_handler.db[self.COLLECTION_NAME]

        match_stage: dict[str, Any] = {"account_count": {"$gte": min_accounts}}
        if not include_blocked:
            match_stage["is_blocked"] = False

        pipeline: list[dict[str, Any]] = [
            {
                "$group": {
                    "_id": "$visitor_id",
                    "user_ids": {"$addToSet": "$user_id"},
                    "last_seen_at": {"$max": "$last_seen_at"},
                    "total_access_count": {"$sum": "$access_count"},
                    "is_blocked": {"$max": "$is_blocked"},
                }
            },
            {
                "$project": {
                    "_id": 0,
                    "visitor_id": "$_id",
                    "user_ids": 1,
                    "account_count": {"$size": "$user_ids"},
                    "last_seen_at": 1,
                    "total_access_count": 1,
                    "is_blocked": 1,
                }
            },
            {"$match": match_stage},
            {"$sort": {"account_count": -1, "last_seen_at": -1}},
        ]

        count_pipeline = pipeline + [{"$count": "total"}]
        count_result = await collection.aggregate(count_pipeline).to_list(length=1)
        total = int(count_result[0]["total"]) if count_result else 0

        items_pipeline = pipeline + [{"$skip": skip}, {"$limit": limit}]
        items = await collection.aggregate(items_pipeline).to_list(length=limit)
        return items, total

    async def get_user_fingerprints(self, user_id: str, limit: int = 100) -> list[dict]:
        await self._ensure_indexes()
        collection = self.mongo_handler.db[self.COLLECTION_NAME]
        cursor = (
            collection.find({"user_id": user_id.strip()})
            .sort("last_seen_at", -1)
            .limit(limit)
        )
        docs = await cursor.to_list(length=limit)
        return [self._to_user_link(doc) for doc in docs]

    async def get_visitor_detail(self, visitor_id: str) -> dict[str, Any] | None:
        await self._ensure_indexes()
        collection = self.mongo_handler.db[self.COLLECTION_NAME]
        cursor = collection.find({"visitor_id": visitor_id.strip()}).sort(
            "last_seen_at", -1
        )
        docs = await cursor.to_list(length=500)
        if not docs:
            return None

        accounts = [self._to_user_link(doc) for doc in docs]
        is_blocked = any(doc.get("is_blocked") for doc in docs)
        return {
            "visitor_id": visitor_id.strip(),
            "accounts": accounts,
            "account_count": len(accounts),
            "is_blocked": is_blocked,
        }

    async def block_visitor(
        self,
        visitor_id: str,
        *,
        reason: str | None = None,
        blocked_by: str | None = None,
    ) -> int:
        await self._ensure_indexes()
        now = datetime.now(timezone.utc)
        collection = self.mongo_handler.db[self.COLLECTION_NAME]
        update_set: dict[str, Any] = {
            "is_blocked": True,
            "blocked_at": now,
        }
        if reason:
            update_set["block_reason"] = reason
        if blocked_by:
            update_set["blocked_by"] = blocked_by

        result = await collection.update_many(
            {"visitor_id": visitor_id.strip()},
            {"$set": update_set},
        )
        return int(result.modified_count)

    async def unblock_visitor(self, visitor_id: str) -> int:
        await self._ensure_indexes()
        collection = self.mongo_handler.db[self.COLLECTION_NAME]
        result = await collection.update_many(
            {"visitor_id": visitor_id.strip()},
            {
                "$set": {"is_blocked": False},
                "$unset": {"blocked_at": "", "block_reason": "", "blocked_by": ""},
            },
        )
        return int(result.modified_count)

    @staticmethod
    def _to_user_link(doc: dict[str, Any]) -> dict[str, Any]:
        return {
            "user_id": doc["user_id"],
            "visitor_id": doc.get("visitor_id"),
            "first_seen_at": doc.get("first_seen_at"),
            "last_seen_at": doc.get("last_seen_at"),
            "access_count": int(doc.get("access_count", 0)),
            "is_blocked": bool(doc.get("is_blocked")),
            "ip_addresses": doc.get("ip_addresses") or [],
            "user_agents": doc.get("user_agents") or [],
            "metadata": doc.get("metadata") or {},
        }
