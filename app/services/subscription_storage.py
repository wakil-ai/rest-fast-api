from app.core.config import settings
from app.core.dependencies import get_mongo_handler
from app.core.logger import logger


class SubscriptionStorage:
    def __init__(self):
        self.mongo_handler = get_mongo_handler()
        self.users_collection = settings.USERS_COLLECTION
        self.subscriptions_collection = settings.SUBSCRIPTIONS_COLLECTION
        self.daily_subscriptions_collection = settings.DAILY_SUBSCRIPTIONS_COLLECTION
        self._indexes_ready = False

    async def ensure_indexes(self) -> None:
        if self._indexes_ready:
            return

        try:
            await self.mongo_handler.db[self.subscriptions_collection].create_index(
                [("user_id", 1)], unique=True
            )
            await self.mongo_handler.db[self.subscriptions_collection].create_index(
                [("end_ms", -1)]
            )
            await self.mongo_handler.db[
                self.daily_subscriptions_collection
            ].create_index([("user_id", 1)], unique=True)
            await self.mongo_handler.db[
                self.daily_subscriptions_collection
            ].create_index([("end_ms", -1)])
            self._indexes_ready = True
        except Exception as exc:
            logger.warning(
                f"[SubscriptionStorage] Failed to ensure subscription indexes: {exc}"
            )

    async def _get_user_document(self, user_id: str) -> dict | None:
        users = self.mongo_handler.db[self.users_collection]

        user = await users.find_one({"_id": user_id})
        if user:
            return user

        return await users.find_one({"user_id": user_id})

    async def get_subscription(self, user_id: str) -> dict | None:
        await self.ensure_indexes()

        subscription = await self.mongo_handler.db[
            self.subscriptions_collection
        ].find_one({"user_id": user_id})
        if subscription:
            return subscription

        user = await self._get_user_document(user_id)
        if not user:
            return None

        legacy_subscription = user.get("subscription")
        if isinstance(legacy_subscription, dict):
            return legacy_subscription

        return None

    async def get_daily_subscription(self, user_id: str) -> dict | None:
        await self.ensure_indexes()

        daily_subscription = await self.mongo_handler.db[
            self.daily_subscriptions_collection
        ].find_one({"user_id": user_id})
        if daily_subscription:
            return daily_subscription

        user = await self._get_user_document(user_id)
        if not user:
            return None

        legacy_daily_subscription = user.get("daily_pass")
        if isinstance(legacy_daily_subscription, dict):
            return legacy_daily_subscription

        return None

    async def upsert_subscription(
        self,
        *,
        user_id: str,
        quote: dict,
        order_id: str,
        transaction_id: str | None,
        now_ms: int,
        provider: str | None,
    ) -> dict:
        await self.ensure_indexes()

        is_daily_subscription = (
            quote.get("tier") == "daily" and quote.get("period") == "daily"
        )

        existing_record = (
            await self.get_daily_subscription(user_id)
            if is_daily_subscription
            else await self.get_subscription(user_id)
        )
        existing_end_ms = (
            int(existing_record.get("end_ms") or 0)
            if isinstance(existing_record, dict)
            else 0
        )

        start_ms = max(now_ms, existing_end_ms)
        end_ms = start_ms + int(quote["days"]) * 24 * 60 * 60 * 1000

        document = {
            "user_id": user_id,
            "tier": quote["tier"],
            "period": quote["period"],
            "daily_credits": int(quote["daily_credits"]),
            "days": int(quote["days"]),
            "total_credits": int(
                quote.get("total_credits")
                or int(quote["daily_credits"]) * int(quote["days"])
            ),
            "amount_sum": quote.get("amount_sum"),
            "start_ms": start_ms,
            "end_ms": end_ms,
            "last_order_id": order_id,
            "last_transaction_id": transaction_id,
            "provider": provider,
            "updated_at_ms": now_ms,
        }

        target_collection = (
            self.daily_subscriptions_collection
            if is_daily_subscription
            else self.subscriptions_collection
        )

        await self.mongo_handler.db[target_collection].update_one(
            {"user_id": user_id},
            {
                "$set": document,
                "$setOnInsert": {"created_at_ms": now_ms},
            },
            upsert=True,
        )

        return document
