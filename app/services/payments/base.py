import secrets
import time

from app.core.config import settings
from app.core.dependencies import get_mongo_handler, get_subscription_storage


class BasePaymentService:
    provider: str = "payment"

    def __init__(self):
        self.db_handler = get_mongo_handler()
        self.users_collection = settings.USERS_COLLECTION
        self.invoices_collection = settings.PAYMENT_INVOICES_COLLECTION
        self.subscription_storage = get_subscription_storage()
        self._invoice_indexes_ready = False

        self._subscription_catalog = {
            "daily": {
                "daily_credits": 300,
                "daily": {
                    "price_sum": settings.PAYME_SUBSCRIPTION_DAILY_PRICE_SUM,
                    "days": 1,
                },
            },
            "standard": {
                "daily_credits": 200,
                "monthly": {
                    "price_sum": settings.PAYME_SUBSCRIPTION_STANDARD_MONTHLY_PRICE_SUM,
                    "days": 30,
                },
                "yearly": {
                    "price_sum": settings.PAYME_SUBSCRIPTION_STANDARD_YEARLY_PRICE_SUM,
                    "days": 360,
                },
            },
            "pro": {
                "daily_credits": 400,
                "monthly": {
                    "price_sum": settings.PAYME_SUBSCRIPTION_PRO_MONTHLY_PRICE_SUM,
                    "days": 30,
                },
                "yearly": {
                    "price_sum": settings.PAYME_SUBSCRIPTION_PRO_YEARLY_PRICE_SUM,
                    "days": 360,
                },
            },
        }

        if settings.DEVELOPMENT_MODE:
            self._subscription_catalog["test"] = {
                "daily_credits": 70,
                "monthly": {
                    "price_sum": settings.PAYME_SUBSCRIPTION_TEST_MONTHLY_PRICE_SUM,
                    "days": 30,
                },
                "yearly": {
                    "price_sum": settings.PAYME_SUBSCRIPTION_TEST_YEARLY_PRICE_SUM,
                    "days": 360,
                },
            }

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

    def _get_subscription_quote(self, tier: str, period: str) -> dict:
        tier_cfg = self._subscription_catalog.get(tier)
        if not tier_cfg:
            raise ValueError("Invalid subscription tier")

        period_cfg = tier_cfg.get(period)
        if not period_cfg:
            raise ValueError("Invalid subscription period")

        daily = int(tier_cfg["daily_credits"])
        days = int(period_cfg["days"])
        price_sum = int(period_cfg["price_sum"])
        return {
            "tier": tier,
            "period": period,
            "daily_credits": daily,
            "days": days,
            "total_credits": daily * days,
            "amount_sum": price_sum,
        }

    def get_subscription_catalog(self) -> list[dict]:
        plans: list[dict] = []
        for tier, cfg in self._subscription_catalog.items():
            daily = int(cfg.get("daily_credits") or 0)
            for period in ("daily", "monthly", "yearly"):
                if period not in cfg:
                    continue
                quote = self._get_subscription_quote(tier, period)
                plans.append(
                    {
                        "tier": quote["tier"],
                        "period": quote["period"],
                        "amount_sum": quote["amount_sum"],
                        "daily_credits": daily,
                        "total_credits": quote["total_credits"],
                        "days": quote["days"],
                    }
                )

        plans.sort(key=lambda plan: (plan["tier"], plan["period"]))
        return plans

    async def get_user_subscription(self, user_id: str) -> dict:
        sub = await self.subscription_storage.get_subscription(user_id)
        daily_pass = await self.subscription_storage.get_daily_subscription(user_id)

        now_ms = int(time.time() * 1000)

        daily_pass_active = False
        daily_pass_daily = None
        daily_pass_start_ms = None
        daily_pass_end_ms = None
        if isinstance(daily_pass, dict):
            daily_pass_daily = int(daily_pass.get("daily_credits") or 0)
            daily_pass_start_ms = daily_pass.get("start_ms")
            daily_pass_end_ms = daily_pass.get("end_ms")
            daily_pass_active = bool(
                int(daily_pass_end_ms or 0) > now_ms and daily_pass_daily > 0
            )

        if not isinstance(sub, dict):
            combined = (daily_pass_daily or 0) if daily_pass_active else 0
            return {
                "user_id": user_id,
                "active": False,
                "daily_pass_active": daily_pass_active,
                "daily_pass_daily_credits": daily_pass_daily,
                "daily_pass_start_ms": daily_pass_start_ms,
                "daily_pass_end_ms": daily_pass_end_ms,
                "combined_daily_credits": combined,
            }

        end_ms = int(sub.get("end_ms") or 0)
        active = bool(end_ms > now_ms and (sub.get("daily_credits") or 0) > 0)
        sub_daily = int(sub.get("daily_credits") or 0)
        combined_daily = (sub_daily if active else 0) + (
            (daily_pass_daily or 0) if daily_pass_active else 0
        )

        return {
            "user_id": user_id,
            "active": active,
            "tier": sub.get("tier"),
            "period": sub.get("period"),
            "daily_credits": sub.get("daily_credits"),
            "start_ms": sub.get("start_ms"),
            "end_ms": sub.get("end_ms"),
            "daily_pass_active": daily_pass_active,
            "daily_pass_daily_credits": daily_pass_daily,
            "daily_pass_start_ms": daily_pass_start_ms,
            "daily_pass_end_ms": daily_pass_end_ms,
            "combined_daily_credits": combined_daily,
        }

    def _resolve_purpose(self, quote: dict | None) -> str:
        if not quote:
            return "payment"
        if quote.get("tier") == "daily" and quote.get("period") == "daily":
            return "daily_pass"
        return "subscription"

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
            expected = quote["amount_sum"]
            if amount_sum is not None and int(amount_sum) != expected:
                raise ValueError("Amount does not match subscription price")
            amount_sum = expected

        if not isinstance(amount_sum, int) or amount_sum <= 0:
            raise ValueError("Invalid amount")

        user = await self.get_user_by_id(user_id)
        if not user:
            raise ValueError("User not found")

        purpose = self._resolve_purpose(quote)
        now_ms = int(time.time() * 1000)

        if quote and not order_id:
            existing_invoice = await self.db_handler.find_one(
                self.invoices_collection,
                {
                    "user_id": user_id,
                    "provider": self.provider,
                    "purpose": purpose,
                    "status": "pending",
                    "subscription.tier": quote.get("tier"),
                    "subscription.period": quote.get("period"),
                },
            )
            if existing_invoice:
                existing_order_id = existing_invoice.get(
                    "order_id"
                ) or existing_invoice.get("invoice_id")
                if existing_order_id:
                    link = await self.build_payment_link(
                        amount_sum=int(
                            existing_invoice.get("amount_sum") or amount_sum
                        ),
                        user_id=user_id,
                        callback_url=str(
                            existing_invoice.get("callback_url") or callback_url
                        ),
                        order_id=str(existing_order_id),
                    )
                    return {"order_id": str(existing_order_id), "link": link}

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
