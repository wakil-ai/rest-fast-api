import secrets
import time
from datetime import datetime, timezone

from core.config import settings
from core.subscription_promo import apply_discount, get_active_campaign
from core.subscription_tiers import (
    SUBSCRIPTION_PERIODS,
    daily_pass_rank,
    is_daily_pass_quote,
)
from core.dependencies import (
    get_mongo_handler,
    get_rate_limit_service,
    get_subscription_storage,
)
from core.logger import logger
from models.payment import SubscriptionEligibilityError


def build_subscription_catalog() -> dict[str, dict]:
    """Tier -> period -> plan config, read fresh from settings on every call.

    Module-level so tests exercise the real table rather than a hand-copied
    duplicate, and so a price override in the environment is picked up without
    constructing a service (which would need Mongo).
    """
    # Paid tiers grant a pool of `total_credits` valid until `end_ms`.
    # Daily passes (basic/standard/premium + period daily) add per-day credits
    # on top of the free quota; multi-month pool tiers have no per-day cap.
    # Pool credits scale strictly with duration — the tier fixes the monthly
    # rate (standard 6000, pro 12000) and every discount is on price only.
    catalog: dict[str, dict] = {
        "basic": {
            "daily": {
                "price_sum": settings.PAYME_SUBSCRIPTION_BASIC_DAILY_PRICE_SUM,
                "days": 1,
                "daily_credits": 200,
                "total_credits": 200,
            },
        },
        "standard": {
            "daily": {
                "price_sum": settings.PAYME_SUBSCRIPTION_STANDARD_DAILY_PRICE_SUM,
                "days": 1,
                "daily_credits": 500,
                "total_credits": 500,
            },
            "monthly": {
                "price_sum": settings.PAYME_SUBSCRIPTION_STANDARD_MONTHLY_PRICE_SUM,
                "previous_price_sum": settings.PAYME_SUBSCRIPTION_STANDARD_MONTHLY_PREVIOUS_PRICE_SUM,
                "days": 30,
                "total_credits": 6000,
            },
            "quarterly": {
                "price_sum": settings.PAYME_SUBSCRIPTION_STANDARD_QUARTERLY_PRICE_SUM,
                "previous_price_sum": settings.PAYME_SUBSCRIPTION_STANDARD_QUARTERLY_PREVIOUS_PRICE_SUM,
                "days": 90,
                "total_credits": 18000,
            },
            "semiannual": {
                "price_sum": settings.PAYME_SUBSCRIPTION_STANDARD_SEMIANNUAL_PRICE_SUM,
                "previous_price_sum": settings.PAYME_SUBSCRIPTION_STANDARD_SEMIANNUAL_PREVIOUS_PRICE_SUM,
                "days": 180,
                "total_credits": 36000,
            },
            "yearly": {
                "price_sum": settings.PAYME_SUBSCRIPTION_STANDARD_YEARLY_PRICE_SUM,
                "previous_price_sum": settings.PAYME_SUBSCRIPTION_STANDARD_YEARLY_PREVIOUS_PRICE_SUM,
                "days": 360,
                "total_credits": 72000,
            },
        },
        "premium": {
            "daily": {
                "price_sum": settings.PAYME_SUBSCRIPTION_PREMIUM_DAILY_PRICE_SUM,
                "days": 1,
                "daily_credits": 800,
                "total_credits": 800,
            },
        },
        "pro": {
            "monthly": {
                "price_sum": settings.PAYME_SUBSCRIPTION_PRO_MONTHLY_PRICE_SUM,
                "days": 30,
                "total_credits": 18000,
            },
            "quarterly": {
                "price_sum": settings.PAYME_SUBSCRIPTION_PRO_QUARTERLY_PRICE_SUM,
                "days": 90,
                "total_credits": 54000,
            },
            "semiannual": {
                "price_sum": settings.PAYME_SUBSCRIPTION_PRO_SEMIANNUAL_PRICE_SUM,
                "days": 180,
                "total_credits": 108000,
            },
            "yearly": {
                "price_sum": settings.PAYME_SUBSCRIPTION_PRO_YEARLY_PRICE_SUM,
                "days": 360,
                "total_credits": 216000,
            },
        },
    }

    if settings.DEBUG:
        catalog["test"] = {
            "monthly": {
                "price_sum": settings.PAYME_SUBSCRIPTION_TEST_MONTHLY_PRICE_SUM,
                "days": 30,
                "total_credits": 2100,
            },
            "quarterly": {
                "price_sum": settings.PAYME_SUBSCRIPTION_TEST_QUARTERLY_PRICE_SUM,
                "days": 90,
                "total_credits": 6300,
            },
            "semiannual": {
                "price_sum": settings.PAYME_SUBSCRIPTION_TEST_SEMIANNUAL_PRICE_SUM,
                "days": 180,
                "total_credits": 12600,
            },
            "yearly": {
                "price_sum": settings.PAYME_SUBSCRIPTION_TEST_YEARLY_PRICE_SUM,
                "days": 360,
                "total_credits": 25200,
            },
        }

    return catalog


class BasePaymentService:
    provider: str = "payment"

    def __init__(self):
        self.db_handler = get_mongo_handler()
        self.users_collection = settings.USERS_COLLECTION
        self.invoices_collection = settings.PAYMENT_INVOICES_COLLECTION
        self.subscription_storage = get_subscription_storage()
        self.rate_limit_service = get_rate_limit_service()
        self._invoice_indexes_ready = False

        self._subscription_catalog = build_subscription_catalog()

    async def ensure_invoice_indexes(self) -> None:
        if self._invoice_indexes_ready:
            return

        collection = self.db_handler.db[self.invoices_collection]
        for index_name in ("provider_1_order_id_1", "provider_1_invoice_id_1"):
            try:
                await collection.drop_index(index_name)
            except Exception:
                pass

        await collection.create_index(
            [("provider", 1), ("order_id", 1)],
            unique=True,
            partialFilterExpression={"order_id": {"$type": "string"}},
        )
        await collection.create_index(
            [("provider", 1), ("invoice_id", 1)],
            unique=True,
            partialFilterExpression={"invoice_id": {"$type": "string"}},
        )
        await collection.create_index(
            [("provider", 1), ("user_id", 1), ("status", 1), ("purpose", 1)]
        )
        self._invoice_indexes_ready = True

    async def get_user_by_id(self, user_id: str) -> dict | None:
        user = await self.db_handler.find_one(
            self.users_collection, {"user_id": user_id}
        )
        if user:
            return user
        return await self.db_handler.find_one(self.users_collection, {"_id": user_id})

    def _get_today_date(self) -> str:
        return datetime.now(timezone.utc).strftime("%Y-%m-%d")

    def _get_period_cfg(self, tier: str, period: str) -> dict:
        tier_cfg = self._subscription_catalog.get(tier)
        if not tier_cfg:
            raise ValueError("Invalid subscription tier")

        period_cfg = tier_cfg.get(period)
        if not period_cfg:
            raise ValueError("Invalid subscription period")

        return period_cfg

    def _get_list_price_sum(self, tier: str, period: str) -> int:
        """Undiscounted catalog price, ignoring any active promo campaign."""
        return int(self._get_period_cfg(tier, period)["price_sum"])

    def _get_subscription_quote(self, tier: str, period: str) -> dict:
        period_cfg = self._get_period_cfg(tier, period)

        days = int(period_cfg["days"])
        list_price_sum = int(period_cfg["price_sum"])
        total_credits = int(period_cfg["total_credits"])
        # Daily cap only applies to the legacy daily-pass tier; 0 means "no per-day cap".
        daily_credits = int(period_cfg.get("daily_credits") or 0)

        now_ms = int(time.time() * 1000)
        price_sum, promo_list_price_sum, discount_percent, promo_ends_at_ms = (
            apply_discount(tier, period, list_price_sum, now_ms)
        )

        # Only a genuine reduction is worth striking through. A stale or equal
        # value would render the current price crossed out above itself, which
        # reads as a rendering fault rather than a saving.
        previous_price_sum = period_cfg.get("previous_price_sum")
        if previous_price_sum is not None:
            previous_price_sum = int(previous_price_sum)
            if previous_price_sum <= price_sum:
                previous_price_sum = None

        return {
            "tier": tier,
            "period": period,
            "daily_credits": daily_credits,
            "days": days,
            "total_credits": total_credits,
            "amount_sum": price_sum,
            "previous_price_sum": previous_price_sum,
            "list_price_sum": promo_list_price_sum,
            "discount_percent": discount_percent,
            "promo_ends_at_ms": promo_ends_at_ms,
        }

    def get_subscription_catalog(self) -> list[dict]:
        plans: list[dict] = []
        now_ms = int(time.time() * 1000)
        promo_active = get_active_campaign(now_ms) is not None
        # Withdrawn when sales are switched off outright, and (as before) for the
        # duration of a sale.
        daily_withdrawn = not settings.DAILY_PASS_ENABLED or promo_active
        for tier, cfg in self._subscription_catalog.items():
            for period in SUBSCRIPTION_PERIODS:
                if period not in cfg or (period == "daily" and daily_withdrawn):
                    continue
                quote = self._get_subscription_quote(tier, period)
                plans.append(
                    {
                        "tier": quote["tier"],
                        "period": quote["period"],
                        "amount_sum": quote["amount_sum"],
                        "daily_credits": quote["daily_credits"],
                        "total_credits": quote["total_credits"],
                        "days": quote["days"],
                        "previous_price_sum": quote["previous_price_sum"],
                        "list_price_sum": quote["list_price_sum"],
                        "discount_percent": quote["discount_percent"],
                        "promo_ends_at_ms": quote["promo_ends_at_ms"],
                    }
                )

        plans.sort(key=lambda plan: (plan["tier"], plan["period"]))
        return plans

    async def get_user_subscription(self, user_id: str) -> dict:
        sub = await self.subscription_storage.get_subscription(user_id)
        daily_pass = await self.subscription_storage.get_daily_subscription(user_id)
        credit_status = await self.rate_limit_service.get_credit_status(user_id)

        now_ms = int(time.time() * 1000)

        daily_pass_active = False
        daily_pass_daily = None
        daily_pass_start_ms = None
        daily_pass_end_ms = None
        daily_pass_tier = None
        daily_pass_provider = None
        if isinstance(daily_pass, dict):
            daily_pass_daily = int(daily_pass.get("daily_credits") or 0)
            daily_pass_start_ms = daily_pass.get("start_ms")
            daily_pass_end_ms = daily_pass.get("end_ms")
            daily_pass_tier = daily_pass.get("tier")
            daily_pass_provider = daily_pass.get("provider")
            daily_pass_active = bool(
                int(daily_pass_end_ms or 0) > now_ms and daily_pass_daily > 0
            )

        # Derive upload entitlement inline from data already loaded above — no
        # extra DB round-trips. Daily-pass eligibility tracks remaining credits
        # (``has_daily_pass_credits``), not just the open window, to match the gate.
        can_upload = self.rate_limit_service.is_upload_entitled(
            subscription=sub,
            has_daily_pass_credits=bool(credit_status.get("has_daily_pass_credits")),
            has_active_promo=bool(credit_status.get("has_active_promo")),
            on_signup_bonus=bool(credit_status.get("on_signup_bonus")),
        )

        if not isinstance(sub, dict):
            combined = (daily_pass_daily or 0) if daily_pass_active else 0
            return {
                "user_id": user_id,
                "active": False,
                "source": daily_pass_provider if daily_pass_active else None,
                "daily_pass_active": daily_pass_active,
                "daily_pass_tier": daily_pass_tier if daily_pass_active else None,
                "daily_pass_daily_credits": daily_pass_daily,
                "daily_pass_start_ms": daily_pass_start_ms,
                "daily_pass_end_ms": daily_pass_end_ms,
                "combined_daily_credits": combined,
                "effective_daily_credit_limit": credit_status[
                    "effective_daily_credit_limit"
                ],
                "today_credits_used": credit_status["today_credits_used"],
                "today_remaining_credits": credit_status["remaining_credits"],
                "uses_combined_credit_pool": credit_status["uses_combined_credit_pool"],
                "can_upload": can_upload,
            }

        end_ms = int(sub.get("end_ms") or 0)
        sub_daily = int(sub.get("daily_credits") or 0)
        total_credits = int(sub.get("total_credits") or 0)
        is_pool_tier = sub.get("tier") in {"standard", "pro", "test"}
        credits_remaining = (
            int(credit_status["remaining_credits"])
            if is_pool_tier
            else int(sub.get("credits_remaining") or 0)
        )

        if is_pool_tier:
            active = bool(end_ms > now_ms and credits_remaining > 0)
        else:
            active = bool(end_ms > now_ms and sub_daily > 0)

        combined_daily = (sub_daily if active else 0) + (
            (daily_pass_daily or 0) if daily_pass_active else 0
        )

        # Prefer the paid subscription's provider; fall back to the daily pass's.
        source = sub.get("provider") or (
            daily_pass_provider if daily_pass_active else None
        )

        return {
            "user_id": user_id,
            "active": active,
            "source": source,
            "tier": sub.get("tier"),
            "period": sub.get("period"),
            "daily_credits": sub_daily,
            "total_credits": total_credits,
            "credits_remaining": credits_remaining,
            "start_ms": sub.get("start_ms"),
            "end_ms": sub.get("end_ms"),
            "daily_pass_active": daily_pass_active,
            "daily_pass_tier": daily_pass_tier if daily_pass_active else None,
            "daily_pass_daily_credits": daily_pass_daily,
            "daily_pass_start_ms": daily_pass_start_ms,
            "daily_pass_end_ms": daily_pass_end_ms,
            "combined_daily_credits": combined_daily,
            "effective_daily_credit_limit": credit_status[
                "effective_daily_credit_limit"
            ],
            "today_credits_used": credit_status["today_credits_used"],
            "today_remaining_credits": credit_status["remaining_credits"],
            "uses_combined_credit_pool": credit_status["uses_combined_credit_pool"],
            "can_upload": can_upload,
        }

    def _resolve_purpose(self, quote: dict | None) -> str:
        if not quote:
            return "payment"
        if is_daily_pass_quote(quote):
            return "daily_pass"
        return "subscription"

    def _is_daily_pass_quote(self, quote: dict | None) -> bool:
        return is_daily_pass_quote(quote)

    async def _get_active_daily_pass(self, user_id: str, now_ms: int) -> dict | None:
        daily_pass = await self.subscription_storage.get_daily_subscription(user_id)
        if not isinstance(daily_pass, dict):
            return None

        end_ms = int(daily_pass.get("end_ms") or 0)
        if end_ms <= now_ms:
            return None
        return daily_pass

    async def _get_active_subscription(self, user_id: str, now_ms: int) -> dict | None:
        subscription = await self.subscription_storage.get_subscription(user_id)
        if not isinstance(subscription, dict):
            return None

        end_ms = int(subscription.get("end_ms") or 0)
        if end_ms <= now_ms:
            return None
        return subscription

    async def validate_subscription_eligibility(
        self, *, user_id: str, quote: dict | None, now_ms: int | None = None
    ) -> None:
        if not isinstance(quote, dict):
            return

        current_time_ms = now_ms or int(time.time() * 1000)

        if self._is_daily_pass_quote(quote):
            if not settings.DAILY_PASS_ENABLED:
                raise SubscriptionEligibilityError(
                    code="DAILY_PASS_DISABLED",
                    message="Daily passes are not available.",
                )
            if get_active_campaign(current_time_ms) is not None:
                raise SubscriptionEligibilityError(
                    code="DAILY_PASS_DISABLED_DURING_PROMO",
                    message="Daily passes are temporarily unavailable during the sale.",
                )
            active_daily_pass = await self._get_active_daily_pass(
                user_id, current_time_ms
            )
            if active_daily_pass is None:
                return

            new_rank = daily_pass_rank(str(quote.get("tier")))
            old_rank = daily_pass_rank(str(active_daily_pass.get("tier")))
            active_end_ms = int(active_daily_pass.get("end_ms") or 0)

            if new_rank > old_rank:
                return

            if new_rank == old_rank:
                return

            raise SubscriptionEligibilityError(
                code="DAILY_PASS_DOWNGRADE_NOT_ALLOWED",
                message="Downgrading an active daily pass is not allowed.",
                active_daily_pass_end_ms=active_end_ms,
            )

        active_subscription = await self._get_active_subscription(
            user_id, current_time_ms
        )
        if active_subscription is None:
            return

        active_tier = active_subscription.get("tier")
        if active_tier not in {"standard", "pro"}:
            return

        # The only permitted in-period change is Standard -> Pro for the same
        # billing duration. It replaces the current allowance and retains the
        # current renewal date; it must never stack a second subscription month.
        if (
            active_tier == "standard"
            and quote.get("tier") == "pro"
            and quote.get("period") == active_subscription.get("period")
        ):
            return

        raise SubscriptionEligibilityError(
            code="ACTIVE_SUBSCRIPTION_EXISTS",
            message="An active subscription already exists for this user. Transfers are not allowed while it is active.",
            active_subscription_end_ms=int(active_subscription.get("end_ms") or 0),
            active_subscription_tier=str(active_tier),
            active_subscription_period=active_subscription.get("period"),
        )

    async def _apply_standard_to_pro_proration(
        self, *, user_id: str, quote: dict, now_ms: int
    ) -> dict:
        """Apply the unused Standard payment toward an immediate Pro upgrade.

        Store providers calculate this themselves. Local one-off checkouts need
        the adjusted invoice amount explicitly, while the entitlement update is
        handled by SubscriptionStorage with the same retained renewal date.
        """
        if quote.get("tier") != "pro":
            return quote

        active = await self._get_active_subscription(user_id, now_ms)
        if not isinstance(active, dict):
            return quote
        if (
            active.get("tier") != "standard"
            or active.get("period") != quote.get("period")
        ):
            return quote

        start_ms = int(active.get("start_ms") or now_ms)
        end_ms = int(active.get("end_ms") or now_ms)
        period_ms = max(1, end_ms - start_ms)
        remaining_ms = max(0, min(period_ms, end_ms - now_ms))
        standard_paid = int(active.get("amount_sum") or 0)
        if standard_paid <= 0:
            standard_paid = int(
                self._get_subscription_quote("standard", str(quote["period"]))[
                    "amount_sum"
                ]
            )

        unused_standard_value = round(standard_paid * remaining_ms / period_ms)
        full_pro_price = int(quote["amount_sum"])
        prorated_price = max(1, full_pro_price - unused_standard_value)
        return {
            **quote,
            "amount_sum": prorated_price,
            "full_amount_sum": full_pro_price,
            "upgrade_credit_sum": unused_standard_value,
            "upgrade_from_tier": "standard",
        }

    async def _finalize_subscription_invoice(
        self, *, order_id: str | None, transaction_id: str | None, now_ms: int
    ) -> None:
        if not order_id:
            return

        invoice = await self.db_handler.find_one(
            self.invoices_collection,
            {
                "provider": self.provider,
                "$or": [{"order_id": order_id}, {"invoice_id": order_id}],
            },
        )
        if not invoice:
            return

        quote = invoice.get("subscription")
        if not isinstance(quote, dict):
            return

        if invoice.get("subscription_applied") is True:
            return

        user_id = invoice.get("user_id")
        if not user_id:
            return

        user = await self.get_user_by_id(user_id)
        if not user:
            return

        resolution = "granted"
        resolution_update: dict[str, int | str] = {}

        if self._is_daily_pass_quote(quote):
            active_daily_pass = await self._get_active_daily_pass(user_id, now_ms)
            active_end_ms = (
                int(active_daily_pass.get("end_ms") or 0) if active_daily_pass else 0
            )
            if active_end_ms > now_ms:
                new_rank = daily_pass_rank(str(quote.get("tier")))
                old_rank = daily_pass_rank(
                    str(active_daily_pass.get("tier")) if active_daily_pass else None
                )
                if new_rank > old_rank:
                    resolution = "granted_daily_pass_upgrade"
                    resolution_update = {
                        "apply_strategy": "upgrade_daily_pass",
                        "previous_daily_pass_tier": active_daily_pass.get("tier"),
                    }
                elif new_rank == old_rank:
                    resolution = "granted_daily_pass_stack"
                    resolution_update = {
                        "apply_strategy": "stack_daily_pass_lot",
                        "active_daily_pass_end_ms": active_end_ms,
                    }
                else:
                    resolution = "granted_after_payment_conflict"
                    resolution_update = {
                        "active_daily_pass_end_ms": active_end_ms,
                        "apply_strategy": "replace_after_eligibility_conflict",
                    }
                    logger.warning(
                        f"[{self.provider}] Daily pass payment for user {user_id} "
                        f"conflicts with active pass until {active_end_ms}"
                    )

        await self.subscription_storage.upsert_subscription(
            user_id=user_id,
            quote=quote,
            order_id=order_id,
            transaction_id=transaction_id,
            now_ms=now_ms,
            provider=invoice.get("provider"),
        )

        await self.db_handler.update_one(
            self.invoices_collection,
            {
                "provider": self.provider,
                "$or": [{"order_id": order_id}, {"invoice_id": order_id}],
            },
            {
                "subscription_applied": True,
                "updated_at": now_ms,
                "subscription_apply_resolution": resolution,
                "subscription_apply_resolution_at_ms": now_ms,
                **resolution_update,
            },
        )

    def _build_invoice_document(
        self,
        *,
        order_id: str,
        user_id: str,
        amount_sum: int,
        callback_url: str,
        purpose: str,
        quote: dict | None,
        now_ms: int,
    ) -> dict:
        return {
            "order_id": order_id,
            "user_id": user_id,
            "amount_sum": amount_sum,
            "callback_url": callback_url,
            "status": "pending",
            "provider": self.provider,
            "purpose": purpose,
            "subscription": quote,
            "subscription_applied": False,
            "created_at": now_ms,
            "updated_at": now_ms,
        }

    async def build_payment_link(
        self, *, amount_sum: int, user_id: str, callback_url: str, order_id: str
    ) -> str:
        raise NotImplementedError

    async def init_payment(
        self,
        *,
        amount_sum: int | None,
        user_id: str,
        callback_url: str,
        order_id: str | None = None,
        subscription_tier: str | None = None,
        subscription_period: str | None = None,
    ) -> dict:
        await self.ensure_invoice_indexes()

        quote = None
        if subscription_tier or subscription_period:
            if not (subscription_tier and subscription_period):
                raise ValueError(
                    "subscription_tier and subscription_period are both required"
                )
            quote = self._get_subscription_quote(subscription_tier, subscription_period)

        user = await self.get_user_by_id(user_id)
        if not user:
            raise ValueError("User not found")

        now_ms = int(time.time() * 1000)
        await self.validate_subscription_eligibility(
            user_id=user_id,
            quote=quote,
            now_ms=now_ms,
        )

        if quote is not None:
            quote = await self._apply_standard_to_pro_proration(
                user_id=user_id, quote=quote, now_ms=now_ms
            )
            expected = int(quote["amount_sum"])
            if amount_sum is not None and int(amount_sum) != expected:
                raise ValueError("Amount does not match subscription price")
            amount_sum = expected

        if not isinstance(amount_sum, int) or amount_sum <= 0:
            raise ValueError("Invalid amount")

        purpose = self._resolve_purpose(quote)

        order_id_value = order_id or secrets.token_hex(8)
        existing_invoice = await self.db_handler.find_one(
            self.invoices_collection,
            {
                "provider": self.provider,
                "$or": [
                    {"order_id": order_id_value},
                    {"invoice_id": order_id_value},
                ],
            },
        )
        if existing_invoice:
            if (
                existing_invoice.get("user_id") == user_id
                and existing_invoice.get("status") == "pending"
                and existing_invoice.get("provider") == self.provider
            ):
                link = await self.build_payment_link(
                    amount_sum=int(existing_invoice.get("amount_sum") or amount_sum),
                    user_id=user_id,
                    callback_url=str(
                        existing_invoice.get("callback_url") or callback_url
                    ),
                    order_id=order_id_value,
                )
                return {"order_id": order_id_value, "link": link}
            raise ValueError("order_id already exists")

        await self.db_handler.insert_one(
            self.invoices_collection,
            self._build_invoice_document(
                order_id=order_id_value,
                user_id=user_id,
                amount_sum=int(amount_sum),
                callback_url=callback_url,
                purpose=purpose,
                quote=quote,
                now_ms=now_ms,
            ),
        )

        link = await self.build_payment_link(
            amount_sum=int(amount_sum),
            user_id=user_id,
            callback_url=callback_url,
            order_id=order_id_value,
        )
        return {"order_id": order_id_value, "link": link}
