from datetime import datetime, timezone
from typing import Literal

from app.core.config import settings
from app.core.dependencies import get_mongo_handler, get_promo_code_service
from app.core.logger import logger

RateLimitAssistantType = Literal[
    "main", "soliq", "deepresearch", "mamuriy_sud", "shartnoma"
]


class RateLimitService:
    """
    Service to manage credit-based rate limiting for users.
    Tracks daily credit usage and enforces limits.
    Credit costs per assistant type:
    - Main assistant (umumiy): 10 credits per request
    - Soliq specialized assistant: 15 credits per request
    - Deep research / agentic RAG: 25 credits per request
    Daily limit: 100 credits per user (configurable)
    """

    RATE_LIMIT_COLLECTION = settings.RATE_LIMIT_COLLECTION

    def __init__(self):
        self.mongo_handler = get_mongo_handler()
        self._promo_code_service = None

    @property
    def promo_code_service(self):
        """Lazy initialization of PromoCodeService to avoid circular imports."""
        if self._promo_code_service is None:
            self._promo_code_service = get_promo_code_service()
        return self._promo_code_service

    def _get_today_date(self) -> str:
        """Get today's date in YYYY-MM-DD format."""
        return datetime.now(timezone.utc).strftime("%Y-%m-%d")

    def _get_credit_cost(self, assistant_type: RateLimitAssistantType) -> int:
        """Get credit cost for a specific assistant type."""
        # TODO replace with AssistantConfig method
        cost_map = {
            "main": settings.CREDIT_COST_MAIN_ASSISTANT,
            "soliq": settings.CREDIT_COST_SOLIQ_ASSISTANT,
            "deepresearch": settings.CREDIT_COST_DEEPRESEARCH,
            "mamuriy_sud": settings.CREDIT_COST_SUD_ASSISTANT,
            "shartnoma": settings.CREDIT_COST_SUD_ASSISTANT,
        }
        return cost_map[assistant_type]

    async def _get_active_subscription_daily_limit(self, user_id: str) -> int | None:
        """Return subscription daily credits if active, else None."""

        try:
            users = self.mongo_handler.db[settings.USERS_COLLECTION]
            user = await users.find_one({"_id": user_id})
            if not user:
                user = await users.find_one({"user_id": user_id})
            if not user:
                return None

            sub = user.get("subscription")
            if not isinstance(sub, dict):
                return None

            daily = int(sub.get("daily_credits") or 0)
            end_ms = int(sub.get("end_ms") or 0)
            now_ms = int(datetime.now(timezone.utc).timestamp() * 1000)

            if daily > 0 and end_ms > now_ms:
                return daily
            return None
        except Exception:
            return None

    async def check_and_decrement_credits(
        self, user_id: str, assistant_type: RateLimitAssistantType = "main"
    ) -> tuple[bool, int, int]:
        """
        Check if user has enough credits and decrement if available (sync).
        Users with valid promo codes may have custom credit limits or unlimited access.
        """
        try:
            subscription_daily = await self._get_active_subscription_daily_limit(
                user_id
            )

            # Check if user has a promo code and get their credit limit
            (
                has_promo,
                promo_credit_limit,
            ) = await self.promo_code_service.get_user_promo_status(user_id)

            # Calculate total daily limit
            daily_limit = (
                subscription_daily
                if subscription_daily is not None
                else settings.DAILY_CREDITS_LIMIT
            )

            if has_promo:
                if promo_credit_limit is None:
                    # Unlimited credits
                    logger.info(
                        f"[RateLimitService] User {user_id} has unlimited access via promo code"
                    )
                    return True, -1, -1  # -1 indicates unlimited
                else:
                    # ADD promo credits to base credits (subscription or default)
                    daily_limit = daily_limit + promo_credit_limit
            else:
                logger.info(
                    f"[RateLimitService] User {user_id} using default {daily_limit} daily credits"
                )

            credit_cost = self._get_credit_cost(assistant_type)

            today = self._get_today_date()
            collection = self.mongo_handler.db[self.RATE_LIMIT_COLLECTION]

            # Find or create user's credit document for today
            query = {"user_id": user_id, "date": today}
            user_limit = await collection.find_one(query)

            if user_limit:
                credits_used = user_limit.get("credits_used", 0)
                credits_remaining = daily_limit - credits_used

                # Check if user has enough credits
                if credits_remaining < credit_cost:
                    logger.warning(
                        f"[RateLimitService] User {user_id} has insufficient credits for {assistant_type}."
                    )
                    return False, credits_remaining, daily_limit

                # Deduct credits
                await collection.update_one(
                    query,
                    {
                        "$inc": {"credits_used": credit_cost},
                        "$set": {"updated_at": datetime.now(timezone.utc)},
                    },
                )
                new_credits_remaining = credits_remaining - credit_cost
                return True, new_credits_remaining, daily_limit
            else:
                # Create new credit entry for today
                await collection.insert_one(
                    {
                        "user_id": user_id,
                        "date": today,
                        "credits_used": credit_cost,
                        "created_at": datetime.now(timezone.utc),
                        "updated_at": datetime.now(timezone.utc),
                    }
                )
                new_credits_remaining = daily_limit - credit_cost
                return True, new_credits_remaining, daily_limit

        except Exception as e:
            logger.error(
                f"[RateLimitService] Error checking credits for user {user_id}: {str(e)}"
            )
            # On error, allow the request (fail open)
            return True, settings.DAILY_CREDITS_LIMIT, settings.DAILY_CREDITS_LIMIT

    async def get_remaining_credits(self, user_id: str) -> int:
        """
        Get the number of remaining credits for today.
        Returns -1 for users with unlimited access via promo code.

        Args:
            user_id: The user's unique identifier

        Returns:
            int: Number of remaining credits (-1 for unlimited)
        """
        try:
            subscription_daily = await self._get_active_subscription_daily_limit(
                user_id
            )

            # Check if user has a promo code and get their credit limit
            (
                has_promo,
                promo_credit_limit,
            ) = await self.promo_code_service.get_user_promo_status(user_id)

            daily_limit = (
                subscription_daily
                if subscription_daily is not None
                else settings.DAILY_CREDITS_LIMIT
            )
            if has_promo:
                if promo_credit_limit is None:
                    # Unlimited credits
                    logger.info(
                        f"[RateLimitService] User {user_id} has unlimited access"
                    )
                    return -1
                else:
                    daily_limit += promo_credit_limit

            today = self._get_today_date()
            collection = self.mongo_handler.db[self.RATE_LIMIT_COLLECTION]

            query = {"user_id": user_id, "date": today}
            user_limit = await collection.find_one(query)

            if user_limit:
                credits_used = user_limit.get("credits_used", 0)
                remaining = max(0, daily_limit - credits_used)
            else:
                remaining = daily_limit

            return remaining

        except Exception as e:
            logger.error(
                f"[RateLimitService] Error getting remaining credits for user {user_id}: {str(e)}"
            )
            return settings.DAILY_CREDITS_LIMIT

    async def get_daily_credit_limit(self, user_id: str) -> int:
        """Return effective daily credit limit for a user (-1 for unlimited)."""

        subscription_daily = await self._get_active_subscription_daily_limit(user_id)

        (
            has_promo,
            promo_credit_limit,
        ) = await self.promo_code_service.get_user_promo_status(user_id)

        daily_limit = (
            subscription_daily
            if subscription_daily is not None
            else settings.DAILY_CREDITS_LIMIT
        )

        if has_promo:
            if promo_credit_limit is None:
                return -1
            return daily_limit + promo_credit_limit

        return daily_limit

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
