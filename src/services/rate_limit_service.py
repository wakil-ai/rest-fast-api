from datetime import datetime, timezone
from typing import Any, Optional

from core.assistants import AssistantConfig
from core.config import settings
from core.dependencies import (
    get_mongo_handler,
    get_promo_code_service,
    get_subscription_storage,
)
from core.logger import logger
from models.chat import AssistantType


class RateLimitService:
    """
    Service to manage credit-based rate limiting for users.

    Paid subscriptions (standard/pro) spend from a monthly pool stored on the
    subscription document. The pool is only valid until ``end_ms``; unused
    credits expire with the subscription.

    Free users and users without an active paid subscription continue to use
    the per-day quota tracked in the rate-limit collection.

    A daily pass (``basic`` / ``standard`` / ``premium`` with period ``daily``, or
    legacy ``tier='daily'``) layers an additional per-day bonus on top of the free quota.
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

    def _get_signup_credits_used(self, user: dict) -> int:
        return int(user.get("signup_credits_used") or 0)

    def _is_on_signup_bonus(self, user: dict) -> bool:
        """True while the user still has unused welcome credits."""
        if user.get("signup_bonus_exhausted"):
            return False

        used = self._get_signup_credits_used(user)
        if used >= settings.SIGNUP_DAY_CREDITS_LIMIT:
            return False

        # Legacy users created before signup_credits_used tracking.
        if "signup_credits_used" not in user and "signup_bonus_exhausted" not in user:
            created_at = user.get("created_at")
            if isinstance(created_at, datetime):
                if created_at.tzinfo is None:
                    created_at = created_at.replace(tzinfo=timezone.utc)
                if created_at.strftime("%Y-%m-%d") != self._get_today_date():
                    return False
        return True

    def _signup_bonus_status(self, user: dict) -> dict[str, int]:
        limit = settings.SIGNUP_DAY_CREDITS_LIMIT
        used = self._get_signup_credits_used(user)
        remaining = max(0, limit - used)
        return {
            "remaining_credits": remaining,
            "effective_daily_credit_limit": remaining,
            "today_credits_used": used,
        }

    async def _try_consume_signup_bonus(
        self, user_id: str, user: dict, credit_cost: int
    ) -> tuple[bool, int, int] | None:
        """Consume welcome credits if eligible. Returns None to use the daily quota."""
        if not self._is_on_signup_bonus(user):
            return None

        limit = settings.SIGNUP_DAY_CREDITS_LIMIT
        used = self._get_signup_credits_used(user)
        remaining = limit - used
        if remaining < credit_cost:
            logger.warning(
                f"[RateLimitService] User {user_id} has insufficient welcome credits "
                f"({remaining} < {credit_cost})."
            )
            return False, remaining, limit

        users = self.mongo_handler.db[settings.USERS_COLLECTION]
        updated = await users.find_one_and_update(
            {
                "_id": user_id,
                "signup_bonus_exhausted": {"$ne": True},
                "$expr": {
                    "$lte": [
                        {
                            "$add": [
                                {"$ifNull": ["$signup_credits_used", 0]},
                                credit_cost,
                            ]
                        },
                        limit,
                    ]
                },
            },
            {
                "$inc": {"signup_credits_used": credit_cost},
                "$set": {"updated_at": datetime.now(timezone.utc)},
            },
            return_document=True,
        )
        if updated is None:
            fresh = await users.find_one({"_id": user_id})
            if fresh and self._is_on_signup_bonus(fresh):
                used = self._get_signup_credits_used(fresh)
                remaining = max(0, limit - used)
                return False, remaining, limit
            return None

        new_used = self._get_signup_credits_used(updated)
        if new_used >= limit and not updated.get("signup_bonus_exhausted"):
            await users.update_one(
                {"_id": user_id},
                {"$set": {"signup_bonus_exhausted": True}},
            )

        new_remaining = max(0, limit - new_used)
        return True, new_remaining, limit

    def _default_daily_limit_for(self, user: dict | None) -> int:
        """Default daily limit after the welcome pool is exhausted."""
        del user
        return settings.DAILY_CREDITS_LIMIT

    async def _fetch_user(self, user_id: str) -> dict | None:
        users = self.mongo_handler.db[settings.USERS_COLLECTION]
        return await users.find_one({"_id": user_id})

    def _ms_to_date(self, ms: int) -> str:
        """Convert epoch milliseconds to a UTC YYYY-MM-DD string."""
        return datetime.fromtimestamp(ms / 1000, tz=timezone.utc).strftime("%Y-%m-%d")

    async def _get_subscription_period_credits_used(
        self, user_id: str, start_ms: int, end_ms: int
    ) -> int:
        """Sum ``credits_used`` in ``creditusage`` from subscription start to end."""
        if start_ms <= 0 or end_ms <= 0 or end_ms < start_ms:
            return 0

        start_date = self._ms_to_date(start_ms)
        end_date = self._ms_to_date(end_ms)
        collection = self.mongo_handler.db[self.RATE_LIMIT_COLLECTION]

        pipeline = [
            {
                "$match": {
                    "user_id": user_id,
                    "date": {"$gte": start_date, "$lte": end_date},
                }
            },
            {"$group": {"_id": None, "total": {"$sum": "$credits_used"}}},
        ]
        rows = await collection.aggregate(pipeline).to_list(length=1)
        if not rows:
            return 0
        return int(rows[0].get("total") or 0)

    async def get_period_credit_usage_breakdown(
        self, user_id: str, start_ms: int, end_ms: int
    ) -> dict:
        """Per-day ``creditusage`` rows over a subscription window, plus the total.

        The admin diagnostic needs the breakdown, not just the sum: the window is
        day-granular (see ``_ms_to_date``), so it silently includes credits the user
        spent on free or daily-pass days that happen to fall inside it. Showing the
        rows is what makes an unexpected balance explainable.
        """
        if start_ms <= 0 or end_ms <= 0 or end_ms < start_ms:
            return {
                "window_start_date": None,
                "window_end_date": None,
                "total_used_in_window": 0,
                "by_date": [],
            }

        start_date = self._ms_to_date(start_ms)
        end_date = self._ms_to_date(end_ms)
        collection = self.mongo_handler.db[self.RATE_LIMIT_COLLECTION]

        cursor = collection.find(
            {"user_id": user_id, "date": {"$gte": start_date, "$lte": end_date}},
            {"_id": 0, "date": 1, "credits_used": 1},
        ).sort("date", 1)
        rows = await cursor.to_list(length=1000)

        by_date = [
            {"date": row.get("date"), "credits_used": int(row.get("credits_used") or 0)}
            for row in rows
        ]
        return {
            "window_start_date": start_date,
            "window_end_date": end_date,
            "total_used_in_window": sum(row["credits_used"] for row in by_date),
            "by_date": by_date,
        }

    async def _get_pool_credits_remaining(self, user_id: str, sub: dict) -> int:
        """Remaining pool credits from ``creditusage`` over the subscription window.

        Legacy subscriptions may only have decrements on ``credits_remaining``;
        take the higher of the two usage sources so we never over-credit.
        """
        total = int(sub.get("total_credits") or 0)
        start_ms = int(sub.get("start_ms") or 0)
        end_ms = int(sub.get("end_ms") or 0)

        creditusage_used = await self._get_subscription_period_credits_used(
            user_id, start_ms, end_ms
        )
        legacy_used = max(0, total - int(sub.get("credits_remaining") or 0))
        effective_used = max(creditusage_used, legacy_used)
        return max(0, total - effective_used)

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

        remaining = await self._get_pool_credits_remaining(user_id, sub)
        if remaining <= 0:
            return None

        return sub

    async def _get_daily_pass_summary(self, user_id: str) -> dict:
        """Aggregate active paid daily credit lots."""
        try:
            helper = getattr(
                self.subscription_storage, "get_daily_pass_credit_summary", None
            )
            if callable(helper):
                summary = await helper(user_id, self._now_ms())
                if isinstance(summary, dict):
                    return {
                        "active": bool(summary.get("active")),
                        "remaining": int(summary.get("remaining") or 0),
                        "total": int(summary.get("total") or 0),
                        "nearest_end_ms": summary.get("nearest_end_ms"),
                        "latest_end_ms": summary.get("latest_end_ms"),
                        "tier": summary.get("tier"),
                    }

            daily_pass = await self.subscription_storage.get_daily_subscription(user_id)
            if not isinstance(daily_pass, dict):
                return {"active": False, "remaining": 0, "total": 0}

            remaining = int(
                daily_pass.get("credits_remaining")
                if "credits_remaining" in daily_pass
                else daily_pass.get("daily_credits") or 0
            )
            total = int(
                daily_pass.get("total_credits") or daily_pass.get("daily_credits") or 0
            )
            end_ms = int(daily_pass.get("end_ms") or 0)
            if end_ms > self._now_ms() and remaining > 0:
                return {
                    "active": True,
                    "remaining": max(0, remaining),
                    "total": max(0, total),
                    "nearest_end_ms": daily_pass.get("nearest_end_ms") or end_ms,
                    "latest_end_ms": daily_pass.get("latest_end_ms") or end_ms,
                    "tier": daily_pass.get("tier"),
                }
            return {"active": False, "remaining": 0, "total": 0}
        except Exception:
            return {"active": False, "remaining": 0, "total": 0}

    async def _try_consume_daily_pass_credits(
        self, user_id: str, cost: int
    ) -> tuple[bool, int, int] | None:
        """Consume paid daily lots if they can cover the full cost."""
        try:
            helper = getattr(
                self.subscription_storage, "try_consume_daily_pass_credits", None
            )
            if callable(helper):
                result = await helper(user_id, cost, self._now_ms())
                if isinstance(result, dict):
                    return (
                        True,
                        int(result.get("credits_remaining") or 0),
                        int(result.get("total_credits") or 0),
                    )
                if result is not None:
                    summary = await self._get_daily_pass_summary(user_id)
                    remaining = int(summary.get("remaining") or 0)
                    if remaining >= cost:
                        total = int(summary.get("total") or remaining)
                        return True, remaining - cost, total
            return None
        except Exception as exc:
            logger.warning(
                f"[RateLimitService] Failed to consume daily pass credits for "
                f"{user_id}: {exc}"
            )
            return None

    async def _sync_pool_credits_remaining(self, user_id: str, remaining: int) -> None:
        """Keep ``subscriptions.credits_remaining`` aligned with creditusage totals."""
        try:
            await self.mongo_handler.db[settings.SUBSCRIPTIONS_COLLECTION].update_one(
                {"user_id": user_id},
                {
                    "$set": {
                        "credits_remaining": remaining,
                        "updated_at_ms": self._now_ms(),
                    }
                },
            )
        except Exception as exc:
            logger.warning(
                f"[RateLimitService] Failed to sync pool credits for {user_id}: {exc}"
            )

    async def _increment_today_credits_used(self, user_id: str, cost: int) -> None:
        today = self._get_today_date()
        collection = self.mongo_handler.db[self.RATE_LIMIT_COLLECTION]
        query = {"user_id": user_id, "date": today}
        now = datetime.now(timezone.utc)

        await collection.update_one(
            query,
            {
                "$inc": {"credits_used": cost},
                "$set": {"updated_at": now},
                "$setOnInsert": {"created_at": now},
            },
            upsert=True,
        )

    async def _rollback_today_credits_used(self, user_id: str, cost: int) -> None:
        today = self._get_today_date()
        collection = self.mongo_handler.db[self.RATE_LIMIT_COLLECTION]
        await collection.update_one(
            {"user_id": user_id, "date": today},
            {
                "$inc": {"credits_used": -cost},
                "$set": {"updated_at": datetime.now(timezone.utc)},
            },
        )

    async def _consume_pool_credits(
        self, user_id: str, cost: int, pool_sub: dict
    ) -> tuple[bool, int, int]:
        """Consume from the monthly pool via ``creditusage`` over the sub window."""
        total = int(pool_sub.get("total_credits") or 0)
        remaining = await self._get_pool_credits_remaining(user_id, pool_sub)
        if remaining < cost:
            return False, remaining, total

        await self._increment_today_credits_used(user_id, cost)
        new_remaining = await self._get_pool_credits_remaining(user_id, pool_sub)
        if new_remaining < 0:
            await self._rollback_today_credits_used(user_id, cost)
            refreshed = await self._get_pool_credits_remaining(user_id, pool_sub)
            return False, refreshed, total

        await self._sync_pool_credits_remaining(user_id, new_remaining)
        return True, new_remaining, total

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
                allowed, remaining, total = await self._consume_pool_credits(
                    user_id, credit_cost, pool_sub
                )
                if not allowed:
                    logger.warning(
                        f"[RateLimitService] User {user_id} pool insufficient "
                        f"({remaining} < {credit_cost}) for {assistant_type}."
                    )
                    return False, remaining, total
                return True, remaining, total

            daily_pass_summary = await self._get_daily_pass_summary(user_id)
            daily_pass_remaining = int(daily_pass_summary.get("remaining") or 0)
            if daily_pass_remaining >= credit_cost:
                daily_result = await self._try_consume_daily_pass_credits(
                    user_id, credit_cost
                )
                if daily_result is not None:
                    return daily_result

            (
                has_promo,
                promo_credit_limit,
            ) = await self.promo_code_service.get_user_promo_status(user_id)

            # Welcome credits are a one-time free pool. Once the user has an
            # active daily pass or promo entitlement, do not let a small leftover
            # welcome balance block paid/promo usage or hide those credits.
            if daily_pass_remaining <= 0 and not has_promo:
                signup_result = await self._try_consume_signup_bonus(
                    user_id, user, credit_cost
                )
                if signup_result is not None:
                    return signup_result

            # Free / promo path: per-day quota. Paid daily lots are handled above
            # from their own expiring credit pools, so they do not reset at UTC midnight.
            daily_limit = self._default_daily_limit_for(user)

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

            # No bucket for today yet: the user has spent nothing, so the whole
            # daily limit is available. Still check it covers the cost — a limit
            # below the cost (e.g. DAILY_CREDITS_LIMIT=0) must deny the very
            # first request of the day, not just the ones after it.
            if daily_limit < credit_cost:
                logger.warning(
                    f"[RateLimitService] User {user_id} has insufficient credits for {assistant_type}."
                )
                return False, daily_limit, daily_limit

            await self._increment_today_credits_used(user_id, credit_cost)
            return True, daily_limit - credit_cost, daily_limit

        except Exception as e:
            logger.error(
                f"[RateLimitService] Error checking credits for user {user_id}: {str(e)}"
            )
            # On error, fall back to the free quota to avoid blocking traffic —
            # but only when that quota can actually cover the request.
            fallback_limit = settings.DAILY_CREDITS_LIMIT
            try:
                fallback_cost = self._get_credit_cost(assistant_type)
            except Exception:
                fallback_cost = 0
            return fallback_limit >= fallback_cost, fallback_limit, fallback_limit

    async def get_remaining_credits(self, user_id: str) -> int:
        """Remaining credits. For paid subs this is the pool; otherwise today's
        daily remaining. Returns -1 for unlimited promo access."""
        try:
            pool_sub = await self._get_active_pool_subscription(user_id)
            if pool_sub is not None:
                return await self._get_pool_credits_remaining(user_id, pool_sub)

            daily_pass_summary = await self._get_daily_pass_summary(user_id)
            daily_pass_remaining = int(daily_pass_summary.get("remaining") or 0)
            (
                has_promo,
                promo_credit_limit,
            ) = await self.promo_code_service.get_user_promo_status(user_id)

            user = await self._fetch_user(user_id)
            if (
                user
                and self._is_on_signup_bonus(user)
                and daily_pass_remaining <= 0
                and not has_promo
            ):
                return self._signup_bonus_status(user)["remaining_credits"]

            if user is None:
                user = await self._fetch_user(user_id)
            daily_limit = self._default_daily_limit_for(user)

            if has_promo:
                if promo_credit_limit is None:
                    return -1
                daily_limit += promo_credit_limit

            today = self._get_today_date()
            collection = self.mongo_handler.db[self.RATE_LIMIT_COLLECTION]
            user_limit = await collection.find_one({"user_id": user_id, "date": today})

            if user_limit:
                credits_used = user_limit.get("credits_used", 0)
                return daily_pass_remaining + max(0, daily_limit - credits_used)
            return daily_pass_remaining + daily_limit

        except Exception as e:
            logger.error(
                f"[RateLimitService] Error getting remaining credits for user {user_id}: {str(e)}"
            )
            return settings.DAILY_CREDITS_LIMIT

    async def get_credit_status(self, user_id: str) -> dict[str, int | bool]:
        """Return a status dict suitable for the subscription/history endpoints.

        For paid subs, remaining is derived from ``creditusage`` between
        ``start_ms`` and ``end_ms``; ``today_credits_used`` is today's bucket."""
        pool_sub = await self._get_active_pool_subscription(user_id)
        if pool_sub is not None:
            total = int(pool_sub.get("total_credits") or 0)
            remaining = await self._get_pool_credits_remaining(user_id, pool_sub)
            start_ms = int(pool_sub.get("start_ms") or 0)
            end_ms = int(pool_sub.get("end_ms") or 0)
            period_used = await self._get_subscription_period_credits_used(
                user_id, start_ms, end_ms
            )
            return {
                "remaining_credits": remaining,
                "effective_daily_credit_limit": remaining,
                "today_credits_used": await self._get_today_credits_used(user_id),
                "period_credits_used": period_used,
                "uses_combined_credit_pool": True,
                "on_signup_bonus": False,
                # Pool subscription already grants upload; the daily-pass and
                # promo signals are only consulted on non-subscription paths.
                "has_daily_pass_credits": False,
                "has_active_promo": False,
            }

        user = await self._fetch_user(user_id)
        # Pure welcome-pool signal (independent of daily-pass/promo precedence) so
        # the subscription view can derive upload entitlement from this dict.
        on_signup_bonus = bool(user and self._is_on_signup_bonus(user))
        daily_pass_summary = await self._get_daily_pass_summary(user_id)
        daily_pass_remaining = int(daily_pass_summary.get("remaining") or 0)
        # Upload entitlement via a daily pass tracks remaining credits, not just
        # an open window — mirrors the gate in ``can_upload_files``.
        has_daily_pass_credits = daily_pass_remaining > 0
        (
            has_promo,
            _promo_credit_limit,
        ) = await self.promo_code_service.get_user_promo_status(user_id)
        if on_signup_bonus and daily_pass_remaining <= 0 and not has_promo:
            status = self._signup_bonus_status(user)
            return {
                **status,
                "uses_combined_credit_pool": True,
                "on_signup_bonus": True,
                "has_daily_pass_credits": False,
                "has_active_promo": False,
            }

        daily_limit = await self.get_daily_credit_limit(user_id)
        if daily_limit == -1:
            return {
                "remaining_credits": -1,
                "effective_daily_credit_limit": -1,
                "today_credits_used": await self._get_today_credits_used(user_id),
                "uses_combined_credit_pool": True,
                "on_signup_bonus": on_signup_bonus,
                "has_daily_pass_credits": has_daily_pass_credits,
                "has_active_promo": has_promo,
            }

        credits_used = await self._get_today_credits_used(user_id)
        free_and_promo_limit = max(0, daily_limit - daily_pass_remaining)
        return {
            "remaining_credits": daily_pass_remaining
            + max(0, free_and_promo_limit - credits_used),
            "effective_daily_credit_limit": daily_limit,
            "today_credits_used": credits_used,
            "uses_combined_credit_pool": True,
            "on_signup_bonus": on_signup_bonus,
            "has_daily_pass_credits": has_daily_pass_credits,
            "has_active_promo": has_promo,
        }

    async def get_daily_credit_limit(self, user_id: str) -> int:
        """Effective daily credit limit for free / daily-pass / promo users.

        For paid pool subscriptions, returns the remaining pool balance (which is
        the effective maximum the user can still spend), so the existing
        reporting endpoints continue to surface a meaningful number.
        """
        pool_sub = await self._get_active_pool_subscription(user_id)
        if pool_sub is not None:
            return await self._get_pool_credits_remaining(user_id, pool_sub)

        daily_pass_summary = await self._get_daily_pass_summary(user_id)
        daily_pass_remaining = int(daily_pass_summary.get("remaining") or 0)

        (
            has_promo,
            promo_credit_limit,
        ) = await self.promo_code_service.get_user_promo_status(user_id)

        user = await self._fetch_user(user_id)
        if (
            user
            and self._is_on_signup_bonus(user)
            and daily_pass_remaining <= 0
            and not has_promo
        ):
            return self._signup_bonus_status(user)["remaining_credits"]

        daily_limit = self._default_daily_limit_for(user) + daily_pass_remaining

        if has_promo:
            if promo_credit_limit is None:
                return -1
            return daily_limit + promo_credit_limit

        return daily_limit

    def _is_active_pool_subscription(self, subscription: Any) -> bool:
        """True for a paid pool subscription inside its active window.

        Entitlement is by tier + window, *not* credit balance: a paid subscriber
        keeps the entitlement while the subscription is active even if the pool
        is exhausted. Shared by the upload gate and ``is_upload_entitled``.
        """
        return bool(
            isinstance(subscription, dict)
            and subscription.get("tier") in self.POOL_TIERS
            and int(subscription.get("end_ms") or 0) > self._now_ms()
        )

    def is_upload_entitled(
        self,
        *,
        subscription: Optional[dict[str, Any]],
        has_daily_pass_credits: bool,
        has_active_promo: bool,
        on_signup_bonus: bool,
    ) -> bool:
        """Pure upload-entitlement rule (no I/O), shared by every consumer.

        A user may upload when ANY of these hold:
          * an active paid pool subscription, OR
          * an active daily pass with credits remaining, OR
          * an active (non-expired) promo code, limited or unlimited, OR
          * they are still on the one-time signup welcome pool
          (`SIGNUP_DAY_CREDITS_LIMIT` credits, e.g, first 100 credits).

        ``has_daily_pass_credits`` tracks *remaining* daily-pass credits, not just
        an open pass window — the named contract that keeps callers from feeding a
        window-only flag and reintroducing exhausted-pass drift.

        Callers resolve the four facts however is cheapest for them (the gate
        loads them lazily; the subscription view derives them from data it already
        holds) and delegate the decision here so the rule lives in one place.
        """
        return bool(
            self._is_active_pool_subscription(subscription)
            or has_daily_pass_credits
            or has_active_promo
            or on_signup_bonus
        )

    async def can_upload_files(self, user_id: str) -> bool:
        """Return True if the user is entitled to upload files.

        Self-loading gate used by the API entitlement guard. Resolves the four
        eligibility facts cheapest-first (short-circuiting so a paid subscriber
        only pays a single lookup) and defers the decision to
        :meth:`is_upload_entitled`.

        This is a paywall check, so it fails *closed* (returns ``False``) on any
        lookup error, unlike the credit check which fails open.
        """
        try:
            sub = await self.subscription_storage.get_subscription(user_id)
            if self._is_active_pool_subscription(sub):
                return True

            daily_pass_summary = await self._get_daily_pass_summary(user_id)
            if int(daily_pass_summary.get("remaining") or 0) > 0:
                return True

            # Any active (non-expired) promo grants upload — has_promo is already
            # gated on validity/expiry by get_user_promo_status.
            (
                has_promo,
                _promo_credit_limit,
            ) = await self.promo_code_service.get_user_promo_status(user_id)
            if has_promo:
                return True

            user = await self._fetch_user(user_id)
            return bool(user and self._is_on_signup_bonus(user))
        except Exception as e:
            logger.error(
                f"[RateLimitService] Error checking upload entitlement for user "
                f"{user_id}: {str(e)}"
            )
            # Fail closed: deny upload when entitlement cannot be confirmed.
            return False

    async def can_create_organization(self, user_id: str) -> bool:
        """Return True if the user is on an active Pro subscription.

        Organization creation is Pro-only — unlike ``can_upload_files``, a daily
        pass, promo, or the signup bonus does not grant this entitlement.

        This is a paywall check, so it fails *closed* (returns ``False``) on any
        lookup error.
        """
        try:
            sub = await self.subscription_storage.get_subscription(user_id)
            return bool(
                isinstance(sub, dict)
                and sub.get("tier") == "pro"
                and int(sub.get("end_ms") or 0) > self._now_ms()
            )
        except Exception as e:
            logger.error(
                f"[RateLimitService] Error checking organization-creation "
                f"entitlement for user {user_id}: {str(e)}"
            )
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
