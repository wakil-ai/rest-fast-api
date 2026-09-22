from pymongo import ReturnDocument

from core.config import settings
from core.dependencies import get_mongo_handler
from core.logger import logger
from core.subscription_tiers import is_daily_pass_quote


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
            ].drop_index("user_id_1")
        except Exception:
            pass

        try:
            await self.mongo_handler.db[
                self.daily_subscriptions_collection
            ].create_index([("user_id", 1), ("end_ms", 1)])
            await self.mongo_handler.db[
                self.daily_subscriptions_collection
            ].create_index([("user_id", 1), ("credits_remaining", 1), ("end_ms", 1)])
            await self.mongo_handler.db[
                self.daily_subscriptions_collection
            ].create_index(
                [("provider", 1), ("order_id", 1)],
                unique=True,
                partialFilterExpression={"order_id": {"$type": "string"}},
            )
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

    @staticmethod
    def _ensure_credits_remaining(subscription: dict) -> dict:
        """Lazy-backfill credits_remaining for docs that predate the pool model."""
        if "credits_remaining" not in subscription:
            subscription["credits_remaining"] = int(
                subscription.get("total_credits") or 0
            )
        return subscription

    async def get_subscription(self, user_id: str) -> dict | None:
        await self.ensure_indexes()

        subscription = await self.mongo_handler.db[
            self.subscriptions_collection
        ].find_one({"user_id": user_id})
        if subscription:
            return self._ensure_credits_remaining(subscription)

        user = await self._get_user_document(user_id)
        if not user:
            return None

        legacy_subscription = user.get("subscription")
        if isinstance(legacy_subscription, dict):
            return self._ensure_credits_remaining(legacy_subscription)

        return None

    async def get_daily_subscription(self, user_id: str) -> dict | None:
        now_ms = self._now_ms()
        summary = await self.get_daily_pass_credit_summary(user_id, now_ms)
        if summary["active"]:
            return {
                "user_id": user_id,
                "tier": summary["tier"],
                "provider": summary["provider"],
                "period": "daily",
                "daily_credits": summary["remaining"],
                "total_credits": summary["total"],
                "credits_remaining": summary["remaining"],
                "start_ms": summary["start_ms"],
                "end_ms": summary["latest_end_ms"],
                "nearest_end_ms": summary["nearest_end_ms"],
                "latest_end_ms": summary["latest_end_ms"],
                "active_lot_count": summary["active_lot_count"],
            }

        user = await self._get_user_document(user_id)
        if not user:
            return None

        legacy_daily_subscription = user.get("daily_pass")
        if isinstance(legacy_daily_subscription, dict):
            return legacy_daily_subscription

        return None

    def _now_ms(self) -> int:
        import time

        return int(time.time() * 1000)

    async def get_active_daily_subscriptions(
        self, user_id: str, now_ms: int
    ) -> list[dict]:
        await self.ensure_indexes()

        collection = self.mongo_handler.db[self.daily_subscriptions_collection]
        cursor = collection.find(
            {
                "user_id": user_id,
                "end_ms": {"$gt": now_ms},
                "$or": [
                    {"credits_remaining": {"$gt": 0}},
                    {
                        "credits_remaining": {"$exists": False},
                        "daily_credits": {"$gt": 0},
                    },
                ],
            }
        ).sort("end_ms", 1)
        return await cursor.to_list(length=100)

    async def get_daily_pass_credit_summary(
        self, user_id: str, now_ms: int
    ) -> dict:
        lots = await self.get_active_daily_subscriptions(user_id, now_ms)
        if not lots:
            return {
                "active": False,
                "remaining": 0,
                "total": 0,
                "nearest_end_ms": None,
                "latest_end_ms": None,
                "start_ms": None,
                "tier": None,
                "provider": None,
                "active_lot_count": 0,
            }

        remaining = sum(self._daily_lot_remaining(lot) for lot in lots)
        total = sum(
            max(0, int(lot.get("total_credits") or lot.get("daily_credits") or 0))
            for lot in lots
        )
        sorted_lots = sorted(lots, key=lambda lot: int(lot.get("end_ms") or 0))
        latest_lot = max(lots, key=lambda lot: int(lot.get("end_ms") or 0))
        return {
            "active": remaining > 0,
            "remaining": remaining,
            "total": total,
            "nearest_end_ms": int(sorted_lots[0].get("end_ms") or 0),
            "latest_end_ms": int(latest_lot.get("end_ms") or 0),
            "start_ms": min(int(lot.get("start_ms") or now_ms) for lot in lots),
            "tier": latest_lot.get("tier"),
            "provider": latest_lot.get("provider"),
            "active_lot_count": len(lots),
        }

    @staticmethod
    def _daily_lot_remaining(lot: dict) -> int:
        if "credits_remaining" in lot:
            return max(0, int(lot.get("credits_remaining") or 0))
        return max(0, int(lot.get("daily_credits") or 0))

    async def try_consume_daily_pass_credits(
        self, user_id: str, cost: int, now_ms: int
    ) -> dict | None:
        lots = await self.get_active_daily_subscriptions(user_id, now_ms)
        total = sum(self._daily_lot_remaining(lot) for lot in lots)
        if total < cost:
            return None

        collection = self.mongo_handler.db[self.daily_subscriptions_collection]
        remaining_to_consume = cost
        consumed: list[tuple[object, int]] = []

        for lot in lots:
            if remaining_to_consume <= 0:
                break

            lot_remaining = self._daily_lot_remaining(lot)
            spend = min(lot_remaining, remaining_to_consume)
            if spend <= 0:
                continue

            if "credits_remaining" in lot:
                query = {
                    "_id": lot.get("_id"),
                    "user_id": user_id,
                    "end_ms": {"$gt": now_ms},
                    "credits_remaining": {"$gte": spend},
                }
                update = {
                    "$inc": {"credits_remaining": -spend},
                    "$set": {"updated_at_ms": now_ms},
                }
            else:
                query = {
                    "_id": lot.get("_id"),
                    "user_id": user_id,
                    "end_ms": {"$gt": now_ms},
                    "credits_remaining": {"$exists": False},
                    "daily_credits": {"$gte": spend},
                }
                update = {
                    "$set": {
                        "credits_remaining": lot_remaining - spend,
                        "updated_at_ms": now_ms,
                    },
                }

            updated = await collection.find_one_and_update(
                query,
                update,
                return_document=ReturnDocument.AFTER,
            )
            if updated is None:
                for lot_id, rollback_amount in consumed:
                    await collection.update_one(
                        {"_id": lot_id},
                        {
                            "$inc": {"credits_remaining": rollback_amount},
                            "$set": {"updated_at_ms": now_ms},
                        },
                    )
                return None

            consumed.append((lot.get("_id"), spend))
            remaining_to_consume -= spend

        if remaining_to_consume > 0:
            for lot_id, rollback_amount in consumed:
                await collection.update_one(
                    {"_id": lot_id},
                    {
                        "$inc": {"credits_remaining": rollback_amount},
                        "$set": {"updated_at_ms": now_ms},
                    },
                )
            return None

        summary = await self.get_daily_pass_credit_summary(user_id, now_ms)
        return {
            "credits_remaining": int(summary["remaining"]),
            "total_credits": int(summary["total"]),
            "nearest_end_ms": summary["nearest_end_ms"],
            "latest_end_ms": summary["latest_end_ms"],
        }

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

        is_daily_subscription = is_daily_pass_quote(quote)

        existing_record = (
            None if is_daily_subscription else await self.get_subscription(user_id)
        )
        existing_end_ms = (
            int(existing_record.get("end_ms") or 0)
            if isinstance(existing_record, dict)
            else 0
        )

        purchased_total = int(
            quote.get("total_credits")
            or int(quote.get("daily_credits") or 0) * int(quote["days"])
        )

        upgrade_from_tier: str | None = None
        upgrade_at_ms: int | None = None

        if is_daily_subscription:
            daily_credits = int(quote.get("daily_credits") or 0)
            days = int(quote["days"])
            start_ms = now_ms
            end_ms = start_ms + days * 24 * 60 * 60 * 1000
            credits_remaining = purchased_total
        else:
            is_standard_to_pro_upgrade = bool(
                isinstance(existing_record, dict)
                and existing_end_ms > now_ms
                and existing_record.get("tier") == "standard"
                and quote.get("tier") == "pro"
                and existing_record.get("period") == quote.get("period")
            )

            if is_standard_to_pro_upgrade:
                # A plan upgrade replaces the current billing-period allowance; it is
                # not a second month or a credit top-up. Keep the original renewal
                # date and preserve what the customer has already consumed.
                start_ms = int(existing_record.get("start_ms") or now_ms)
                end_ms = existing_end_ms
                old_total = max(0, int(existing_record.get("total_credits") or 0))
                old_remaining = max(
                    0, int(existing_record.get("credits_remaining") or 0)
                )
                credits_used = max(0, old_total - old_remaining)
                credits_remaining = max(0, purchased_total - credits_used)
                upgrade_from_tier = "standard"
                upgrade_at_ms = now_ms
            else:
                start_ms = max(now_ms, existing_end_ms)
                end_ms = start_ms + int(quote["days"]) * 24 * 60 * 60 * 1000

                existing_remaining = 0
                if isinstance(existing_record, dict) and existing_end_ms > now_ms:
                    existing_remaining = max(
                        0, int(existing_record.get("credits_remaining") or 0)
                    )

                credits_remaining = existing_remaining + purchased_total
            daily_credits = int(quote.get("daily_credits") or 0)

        document = {
            "user_id": user_id,
            "tier": quote["tier"],
            "period": quote["period"],
            "daily_credits": daily_credits if is_daily_subscription else int(quote.get("daily_credits") or 0),
            "days": int(quote["days"]),
            "total_credits": purchased_total,
            "credits_remaining": credits_remaining,
            "amount_sum": quote.get("amount_sum"),
            "start_ms": start_ms,
            "end_ms": end_ms,
            "order_id": order_id,
            "transaction_id": transaction_id,
            "last_order_id": order_id,
            "last_transaction_id": transaction_id,
            "provider": provider,
            "upgrade_from_tier": upgrade_from_tier,
            "upgrade_at_ms": upgrade_at_ms,
            "updated_at_ms": now_ms,
        }

        target_collection = (
            self.daily_subscriptions_collection
            if is_daily_subscription
            else self.subscriptions_collection
        )

        collection = self.mongo_handler.db[target_collection]

        if is_daily_subscription and not (provider and order_id):
            await self.mongo_handler.db[target_collection].insert_one(
                {
                    **document,
                    "created_at_ms": now_ms,
                }
            )
            return document

        query = (
            {"provider": provider, "order_id": order_id}
            if is_daily_subscription
            else {"user_id": user_id}
        )

        if is_daily_subscription:
            existing_lot = await collection.find_one(query)
            if existing_lot:
                return existing_lot

            await collection.update_one(
                query,
                {
                    "$setOnInsert": {
                        **document,
                        "created_at_ms": now_ms,
                    },
                },
                upsert=True,
            )
            return document

        await collection.update_one(
            query,
            {
                "$set": document,
                "$setOnInsert": {"created_at_ms": now_ms},
            },
            upsert=True,
        )

        return document

    async def try_consume_pool_credits(
        self, user_id: str, cost: int, now_ms: int
    ) -> dict | None:
        """Atomically decrement credits_remaining if the subscription is still
        active and the pool covers the cost. Returns the updated sub doc on
        success, or None if no active sub or insufficient credits.

        Lazy-backfills credits_remaining for legacy docs that predate the pool
        model by treating absent values as equal to total_credits.
        """

        await self.ensure_indexes()
        collection = self.mongo_handler.db[self.subscriptions_collection]

        # First pass: try the strict decrement assuming credits_remaining exists.
        updated = await collection.find_one_and_update(
            {
                "user_id": user_id,
                "end_ms": {"$gt": now_ms},
                "credits_remaining": {"$gte": cost},
            },
            {
                "$inc": {"credits_remaining": -cost},
                "$set": {"updated_at_ms": now_ms},
            },
            return_document=True,
        )
        if updated is not None:
            return updated

        # Legacy doc: no credits_remaining field. Lazily initialize from
        # total_credits, then decrement in a single atomic update.
        legacy = await collection.find_one(
            {
                "user_id": user_id,
                "end_ms": {"$gt": now_ms},
                "credits_remaining": {"$exists": False},
            }
        )
        if legacy is None:
            return None

        total = int(legacy.get("total_credits") or 0)
        if total < cost:
            return None

        updated = await collection.find_one_and_update(
            {
                "user_id": user_id,
                "end_ms": {"$gt": now_ms},
                "credits_remaining": {"$exists": False},
            },
            {
                "$set": {
                    "credits_remaining": total - cost,
                    "updated_at_ms": now_ms,
                },
            },
            return_document=True,
        )
        return updated

    # ------------------------------------------------------------------
    # Admin surface
    #
    # Every write below funnels through `admin_set_pool_fields` so integer
    # coercion and the optimistic lock live in exactly one place. A String
    # `end_ms` reads back fine over HTTP but never matches the Mongo-side
    # `{"end_ms": {"$gt": now_ms}}` predicate in `try_consume_pool_credits`,
    # leaving a subscriber unable to spend anything.
    # ------------------------------------------------------------------

    _INT_FIELDS = (
        "start_ms",
        "end_ms",
        "total_credits",
        "credits_remaining",
        "daily_credits",
        "days",
    )

    @classmethod
    def _coerce_int_fields(cls, updates: dict) -> dict:
        coerced = dict(updates)
        for field in cls._INT_FIELDS:
            if field in coerced and coerced[field] is not None:
                coerced[field] = int(coerced[field])
        return coerced

    async def get_raw_subscription(self, user_id: str) -> dict | None:
        """The stored document, untouched.

        Unlike `get_subscription`, this applies no `credits_remaining` backfill and
        no legacy `users.subscription` fallback — the diagnostic view has to show
        what is actually on disk, including the fields that are missing.
        """
        await self.ensure_indexes()
        return await self.mongo_handler.db[self.subscriptions_collection].find_one(
            {"user_id": user_id}
        )

    async def get_all_daily_lots(self, user_id: str, limit: int = 50) -> list[dict]:
        """Every daily-pass lot, expired ones included, newest window first."""
        await self.ensure_indexes()
        cursor = (
            self.mongo_handler.db[self.daily_subscriptions_collection]
            .find({"user_id": user_id})
            .sort("end_ms", -1)
            .limit(limit)
        )
        return await cursor.to_list(length=limit)

    async def admin_set_pool_fields(
        self,
        user_id: str,
        *,
        updates: dict,
        now_ms: int,
        expected_updated_at_ms: int | None = None,
    ) -> dict | None:
        """Set fields on the pool subscription under an optimistic lock.

        Returns the updated document, or ``None`` when the guard did not match —
        either no subscription exists or another writer moved it first. The caller
        decides which of those it is.
        """
        query: dict = {"user_id": user_id}
        if expected_updated_at_ms is not None:
            query["updated_at_ms"] = expected_updated_at_ms

        payload = self._coerce_int_fields(updates)
        payload["updated_at_ms"] = int(now_ms)

        return await self.mongo_handler.db[
            self.subscriptions_collection
        ].find_one_and_update(
            query,
            {"$set": payload},
            return_document=ReturnDocument.AFTER,
        )

    async def admin_set_daily_lot_fields(
        self, lot_id, user_id: str, *, updates: dict, now_ms: int
    ) -> dict | None:
        payload = self._coerce_int_fields(updates)
        payload["updated_at_ms"] = int(now_ms)

        return await self.mongo_handler.db[
            self.daily_subscriptions_collection
        ].find_one_and_update(
            {"_id": lot_id, "user_id": user_id},
            {"$set": payload},
            return_document=ReturnDocument.AFTER,
        )

    async def admin_revoke_daily_lots(
        self,
        user_id: str,
        *,
        now_ms: int,
        lot_ids: list | None = None,
        zero_credits: bool = True,
        operator: str | None = None,
        reason: str | None = None,
    ) -> int:
        """Expire daily lots in place. Never deletes — the history stays readable."""
        query: dict = {"user_id": user_id, "end_ms": {"$gt": int(now_ms)}}
        if lot_ids is not None:
            query = {"_id": {"$in": lot_ids}, "user_id": user_id}

        updates: dict = {
            "end_ms": int(now_ms) - 1,
            "updated_at_ms": int(now_ms),
            "revoked_at_ms": int(now_ms),
            "revoked_by": operator,
            "revoke_reason": reason,
        }
        if zero_credits:
            updates["credits_remaining"] = 0

        result = await self.mongo_handler.db[
            self.daily_subscriptions_collection
        ].update_many(query, {"$set": updates})
        return int(getattr(result, "modified_count", 0) or 0)
