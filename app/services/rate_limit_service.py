from datetime import datetime, timezone

from app.core.assistants import AssistantConfig
from app.core.config import settings
from app.core.dependencies import (
    get_mongo_handler,
    get_promo_code_service,
    get_subscription_storage,
)
from app.core.logger import logger
from app.models.chat import AssistantType


class RateLimitService:
    """
    Service to manage credit-based rate limiting for users.

    Paid subscriptions (standard/pro) spend from a monthly pool stored on the
    subscription document. The pool is only valid until ``end_ms``; unused
    credits expire with the subscription.

    Free users and users without an active paid subscription continue to use
    the per-day quota tracked in the rate-limit collection.

    A legacy "daily pass" tier (``tier='daily'``) layers an additional per-day
    bonus on top of the free quota.
    """

    RATE_LIMIT_COLLECTION = settings.RATE_LIMIT_COLLECTION
    POOL_TIERS = {"standard", "pro", "test"}

    def __init__(self):
        self.mongo_handler = get_mongo_handler()
        self.promo_code_service = get_promo_code_service()
        self.subscription_storage = get_subscription_storage()

    def _get_today_date(self) -> str:
        """Get today's date in YYYY-MM-DD format."""
        return datetime.now(timezone.utc).strftime("%Y-%m-%d")

    def _now_ms(self) -> int:
        return int(datetime.now(timezone.utc).timestamp() * 1000)

    def _get_credit_cost(self, assistant_type: AssistantType) -> int:
        """Get credit cost for a specific assistant type."""
        return AssistantConfig.get_credit_cost(assistant_type)

    async def _get_today_credits_used(self, user_id: str) -> int:
        today = self._get_today_date()
        collection = self.mongo_handler.db[self.RATE_LIMIT_COLLECTION]
        user_limit = await collection.find_one({"user_id": user_id, "date": today})
        return int(user_limit.get("credits_used", 0)) if user_limit else 0

    def _default_daily_limit_for(self, user: dict | None) -> int:
        """Default daily limit, granting the signup-day bonus if registered today (UTC)."""
        if isinstance(user, dict):
            created_at = user.get("created_at")
            if isinstance(created_at, datetime):
                if created_at.tzinfo is None:
                    created_at = created_at.replace(tzinfo=timezone.utc)
                if created_at.strftime("%Y-%m-%d") == self._get_today_date():
                    return settings.SIGNUP_DAY_CREDITS_LIMIT
        return settings.DAILY_CREDITS_LIMIT

    async def _fetch_user(self, user_id: str) -> dict | None:
        users = self.mongo_handler.db[settings.USERS_COLLECTION]
        return await users.find_one({"_id": user_id})

    async def _get_active_pool_subscription(self, user_id: str) -> dict | None:
        """Return the active pool-based subscription doc, or None."""
        try:
            sub = await self.subscription_storage.get_subscription(user_id)
        except Exception:
            return None

        if not isinstance(sub, dict):
            return None

        if sub.get("tier") not in self.POOL_TIERS:
            return None

        end_ms = int(sub.get("end_ms") or 0)
        if end_ms <= self._now_ms():
            return None

        remaining = int(sub.get("credits_remaining") or 0)
        if remaining <= 0:
            return None

        return sub

    async def _get_active_daily_pass_bonus(self, user_id: str) -> int:
        """Return active daily-pass credits to add to the free daily limit."""
        try:
            daily_pass = await self.subscription_storage.get_daily_subscription(user_id)
            if not isinstance(daily_pass, dict):
                return 0

            daily = int(daily_pass.get("daily_credits") or 0)
            end_ms = int(daily_pass.get("end_ms") or 0)
            if end_ms > self._now_ms() and "daily_credits" in daily_pass:
                return max(0, daily)
            return 0
        except Exception:
            return 0

    async def _consume_pool_credits(self, user_id: str, cost: int) -> dict | None:
        """Attempt to consume `cost` from the pool subscription; returns updated doc or None."""
        return await self.subscription_storage.try_consume_pool_credits(
            user_id, cost, self._now_ms()
        )

    async def check_and_decrement_credits(
        self, user_id: str, assistant_type: AssistantType = "main"
    ) -> tuple[bool, int, int]:
        """
        Check if user has enough credits and decrement if available.

        Returns (allowed, remaining, limit).
          * Paid subscription: remaining/limit reflect the monthly pool.
          * Free or daily-pass user: remaining/limit reflect today's daily quota.
          * Unlimited promo: returns (True, -1, -1).
        """
        try:
            users = self.mongo_handler.db[settings.USERS_COLLECTION]
            user = await users.find_one({"_id": user_id})

            if not user:
                logger.warning(f"[RateLimitService] Invalid user ID: {user_id}")
                return False, 0, 0

            credit_cost = self._get_credit_cost(assistant_type)

            # Paid subscription: spend from the monthly pool. Promo codes do
            # not stack on top of an active paid subscription.
            pool_sub = await self._get_active_pool_subscription(user_id)
            if pool_sub is not None:
                updated = await self._consume_pool_credits(user_id, credit_cost)
                total = int(pool_sub.get("total_credits") or 0)
                if updated is None:
                    remaining = int(pool_sub.get("credits_remaining") or 0)
                    logger.warning(
                        f"[RateLimitService] User {user_id} pool insufficient "
                        f"({remaining} < {credit_cost}) for {assistant_type}."
                    )
                    return False, remaining, total
                new_remaining = int(updated.get("credits_remaining") or 0)
                return True, new_remaining, total

            # Free / daily-pass / promo path: per-day quota.
            daily_pass_bonus = await self._get_active_daily_pass_bonus(user_id)
            (
                has_promo,
                promo_credit_limit,
            ) = await self.promo_code_service.get_user_promo_status(user_id)

            daily_limit = self._default_daily_limit_for(user) + daily_pass_bonus

            if has_promo:
                if promo_credit_limit is None:
                    logger.info(
                        f"[RateLimitService] User {user_id} has unlimited access via promo code"
                    )
                    return True, -1, -1
                daily_limit += promo_credit_limit
            else:
                logger.info(
                    f"[RateLimitService] User {user_id} using {daily_limit} daily credits"
                )

            today = self._get_today_date()
            collection = self.mongo_handler.db[self.RATE_LIMIT_COLLECTION]
            query = {"user_id": user_id, "date": today}
            user_limit = await collection.find_one(query)

            if user_limit:
                credits_used = user_limit.get("credits_used", 0)
                credits_remaining = daily_limit - credits_used

                if credits_remaining < credit_cost:
                    logger.warning(
                        f"[RateLimitService] User {user_id} has insufficient credits for {assistant_type}."
                    )
                    return False, credits_remaining, daily_limit

                await collection.update_one(
                    query,
                    {
                        "$inc": {"credits_used": credit_cost},
                        "$set": {"updated_at": datetime.now(timezone.utc)},
                    },
                )
                return True, credits_remaining - credit_cost, daily_limit

            await collection.insert_one(
                {
                    "user_id": user_id,
                    "date": today,
                    "credits_used": credit_cost,
                    "created_at": datetime.now(timezone.utc),
                    "updated_at": datetime.now(timezone.utc),
                }
            )
            return True, daily_limit - credit_cost, daily_limit

        except Exception as e:
            logger.error(
                f"[RateLimitService] Error checking credits for user {user_id}: {str(e)}"
            )
            # On error, fail open with the free quota to avoid blocking traffic.
            return True, settings.DAILY_CREDITS_LIMIT, settings.DAILY_CREDITS_LIMIT

    async def get_remaining_credits(self, user_id: str) -> int:
        """Remaining credits. For paid subs this is the pool; otherwise today's
        daily remaining. Returns -1 for unlimited promo access."""
        try:
            pool_sub = await self._get_active_pool_subscription(user_id)
            if pool_sub is not None:
                return int(pool_sub.get("credits_remaining") or 0)

            daily_pass_bonus = await self._get_active_daily_pass_bonus(user_id)
            (
                has_promo,
                promo_credit_limit,
            ) = await self.promo_code_service.get_user_promo_status(user_id)

            user = await self._fetch_user(user_id)
            daily_limit = self._default_daily_limit_for(user) + daily_pass_bonus

            if has_promo:
                if promo_credit_limit is None:
                    return -1
                daily_limit += promo_credit_limit

            today = self._get_today_date()
            collection = self.mongo_handler.db[self.RATE_LIMIT_COLLECTION]
            user_limit = await collection.find_one({"user_id": user_id, "date": today})

            if user_limit:
                credits_used = user_limit.get("credits_used", 0)
                return max(0, daily_limit - credits_used)
            return daily_limit

        except Exception as e:
            logger.error(
                f"[RateLimitService] Error getting remaining credits for user {user_id}: {str(e)}"
            )
            return settings.DAILY_CREDITS_LIMIT

    async def get_credit_status(self, user_id: str) -> dict[str, int | bool]:
        """Return a status dict suitable for the subscription/history endpoints.

        For paid subs the daily fields reflect the pool (today_credits_used is 0
        since the pool is not date-bucketed)."""
        pool_sub = await self._get_active_pool_subscription(user_id)
        if pool_sub is not None:
            total = int(pool_sub.get("total_credits") or 0)
            remaining = int(pool_sub.get("credits_remaining") or 0)
            return {
                "remaining_credits": remaining,
                "effective_daily_credit_limit": remaining,
                "today_credits_used": max(0, total - remaining),
                "uses_combined_credit_pool": True,
            }

        daily_limit = await self.get_daily_credit_limit(user_id)
        if daily_limit == -1:
            return {
                "remaining_credits": -1,
                "effective_daily_credit_limit": -1,
                "today_credits_used": await self._get_today_credits_used(user_id),
                "uses_combined_credit_pool": True,
            }

        credits_used = await self._get_today_credits_used(user_id)
        return {
            "remaining_credits": max(0, daily_limit - credits_used),
            "effective_daily_credit_limit": daily_limit,
            "today_credits_used": credits_used,
            "uses_combined_credit_pool": True,
        }

    async def get_daily_credit_limit(self, user_id: str) -> int:
        """Effective daily credit limit for free / daily-pass / promo users.

        For paid pool subscriptions, returns the remaining pool balance (which is
        the effective maximum the user can still spend), so the existing
        reporting endpoints continue to surface a meaningful number.
        """
        pool_sub = await self._get_active_pool_subscription(user_id)
        if pool_sub is not None:
            return int(pool_sub.get("credits_remaining") or 0)

        daily_pass_bonus = await self._get_active_daily_pass_bonus(user_id)

        (
            has_promo,
            promo_credit_limit,
        ) = await self.promo_code_service.get_user_promo_status(user_id)

        user = await self._fetch_user(user_id)
        daily_limit = self._default_daily_limit_for(user) + daily_pass_bonus

        if has_promo:
            if promo_credit_limit is None:
                return -1
            return daily_limit + promo_credit_limit

        return daily_limit

    async def can_upload_files(self, user_id: str) -> bool:
        """Return True if the user is entitled to upload files.

        File upload is a paid-plan *tier* entitlement, so a paid subscriber
        qualifies while their subscription is within its active window —
        regardless of how much of the credit pool is left. A user qualifies if
        they have:
          * an active paid subscription (``tier`` in ``POOL_TIERS`` and
            ``end_ms`` in the future), OR
          * an active daily pass, OR
          * an unlimited promo code.

        This is a paywall check, so it fails *closed* (returns ``False``) on any
        lookup error, unlike the credit check which fails open.
        """
        try:
            # Active paid subscription, by tier + window (not credit balance).
            sub = await self.subscription_storage.get_subscription(user_id)
            if (
                isinstance(sub, dict)
                and sub.get("tier") in self.POOL_TIERS
                and int(sub.get("end_ms") or 0) > self._now_ms()
            ):
                return True

            # Active daily pass.
            daily_pass_bonus = await self._get_active_daily_pass_bonus(user_id)
            if daily_pass_bonus > 0:
                return True

            has_promo, promo_credit_limit = (
                await self.promo_code_service.get_user_promo_status(user_id)
            )
            # promo_credit_limit is None => unlimited promo access.
            if has_promo and promo_credit_limit is None:
                return True

            return False
        except Exception as e:
            logger.error(
                f"[RateLimitService] Error checking upload entitlement for user "
                f"{user_id}: {str(e)}"
            )
            # Fail closed: deny upload when entitlement cannot be confirmed.
            return False

    def reset_user_limit(self, user_id: str) -> bool:
        """
        Reset rate limit for a specific user (admin function).

        Args:
            user_id: The user's unique identifier

        Returns:
            bool: True if reset successful
        """
        try:
            today = self._get_today_date()
            collection = self.mongo_handler.db[self.RATE_LIMIT_COLLECTION]

            result = collection.delete_one({"user_id": user_id, "date": today})
            logger.info(
                f"[RateLimitService] Reset limit for user {user_id}, deleted {result.deleted_count} records"
            )
            return result.deleted_count > 0

        except Exception as e:
            logger.error(
                f"[RateLimitService] Error resetting limit for user {user_id}: {str(e)}"
            )
            return False
