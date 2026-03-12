import hashlib
import secrets
import time
from decimal import Decimal, InvalidOperation
from urllib.parse import urlencode

from app.core.config import settings
from app.core.dependencies import get_mongo_handler
from app.core.logger import logger


class ClickError:
    SUCCESS = 0
    SIGN_CHECK_FAILED = -1
    INCORRECT_AMOUNT = -2
    ACTION_NOT_FOUND = -3
    ALREADY_PAID = -4
    USER_DOES_NOT_EXIST = -5
    TRANSACTION_DOES_NOT_EXIST = -6
    FAILED_TO_UPDATE_USER = -7
    ERROR_IN_REQUEST = -8
    TRANSACTION_CANCELLED = -9


def _md5_hex(value: str) -> str:
    return hashlib.md5(value.encode("utf-8")).hexdigest()


def _as_int(value) -> int | None:
    try:
        if value is None:
            return None
        return int(str(value))
    except Exception:
        return None


def _as_decimal(value) -> Decimal | None:
    try:
        if value is None:
            return None
        return Decimal(str(value))
    except (InvalidOperation, ValueError):
        return None


class ClickService:
    def __init__(self):
        self.db_handler = get_mongo_handler()
        self.users_collection = settings.USERS_COLLECTION
        self.invoices_collection = settings.CLICK_INVOICES_COLLECTION
        self.transactions_collection = settings.CLICK_TRANSACTIONS_COLLECTION

        self._subscription_catalog = {
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

        user = await self.db_handler.find_one(
            self.users_collection, {"user_id": user_id}
        )
        if not user:
            user = await self.db_handler.find_one(
                self.users_collection, {"_id": user_id}
            )
        if not user:
            raise ValueError("User not found")

        now_ms = int(time.time() * 1000)

        # Reuse an existing pending subscription invoice for the same user + tier + period.
        if quote and not order_id:
            existing_invoice = await self.db_handler.find_one(
                self.invoices_collection,
                {
                    "user_id": user_id,
                    "provider": "click",
                    "purpose": "subscription",
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
                    link = self.create_payment_link(
                        amount_sum=int(
                            existing_invoice.get("amount_sum") or amount_sum
                        ),
                        order_id=str(existing_order_id),
                        callback_url=str(
                            existing_invoice.get("callback_url") or callback_url
                        ),
                    )
                    return {"order_id": str(existing_order_id), "link": link}

        order_id_value = order_id or secrets.token_hex(8)

        existing_invoice = await self.db_handler.find_one(
            self.invoices_collection,
            {"$or": [{"order_id": order_id_value}, {"invoice_id": order_id_value}]},
        )
        if existing_invoice:
            if (
                existing_invoice.get("user_id") == user_id
                and existing_invoice.get("status") == "pending"
                and existing_invoice.get("provider") == "click"
            ):
                link = self.create_payment_link(
                    amount_sum=int(existing_invoice.get("amount_sum") or amount_sum),
                    order_id=order_id_value,
                    callback_url=str(
                        existing_invoice.get("callback_url") or callback_url
                    ),
                )
                return {"order_id": order_id_value, "link": link}
            raise ValueError("order_id already exists")

        await self.db_handler.insert_one(
            self.invoices_collection,
            {
                "order_id": order_id_value,
                "user_id": user_id,
                "amount_sum": int(amount_sum),
                "callback_url": callback_url,
                "status": "pending",
                "provider": "click",
                "purpose": "subscription" if quote else "payment",
                "subscription": quote,
                "subscription_applied": False,
                "created_at": now_ms,
                "updated_at": now_ms,
            },
        )

        link = self.create_payment_link(
            amount_sum=int(amount_sum),
            order_id=order_id_value,
            callback_url=callback_url,
        )
        return {"order_id": order_id_value, "link": link}

    def create_payment_link(
        self, *, amount_sum: int, order_id: str, callback_url: str
    ) -> str:
        merchant_id = getattr(settings, "CLICK_MERCHANT_ID", None)
        service_id = getattr(settings, "CLICK_SERVICE_ID", None)
        if not merchant_id or not service_id:
            raise ValueError("CLICK merchant_id/service_id not configured")

        amount_str = f"{Decimal(int(amount_sum)):.2f}"
        params = {
            "service_id": int(service_id),
            "merchant_id": int(merchant_id),
            "amount": amount_str,
            "transaction_param": str(order_id),
            "return_url": str(callback_url),
        }
        merchant_user_id = getattr(settings, "CLICK_MERCHANT_USER_ID", None)
        if merchant_user_id is not None:
            params["merchant_user_id"] = int(merchant_user_id)

        return f"{settings.CLICK_PAYMENT_LINK_BASE}?{urlencode(params)}"

    def _verify_prepare_signature(
        self,
        *,
        click_trans_id,
        service_id,
        merchant_trans_id,
        amount,
        action,
        sign_time,
        sign_string,
    ) -> bool:
        secret_key = getattr(settings, "CLICK_SECRET_KEY", None)
        if not secret_key:
            return False

        raw = f"{click_trans_id}{service_id}{secret_key}{merchant_trans_id}{amount}{action}{sign_time}"
        expected = _md5_hex(raw)
        return secrets.compare_digest(str(expected), str(sign_string or ""))

    def _verify_complete_signature(
        self,
        *,
        click_trans_id,
        service_id,
        merchant_trans_id,
        merchant_prepare_id,
        amount,
        action,
        sign_time,
        sign_string,
    ) -> bool:
        secret_key = getattr(settings, "CLICK_SECRET_KEY", None)
        if not secret_key:
            return False

        raw = f"{click_trans_id}{service_id}{secret_key}{merchant_trans_id}{merchant_prepare_id}{amount}{action}{sign_time}"
        expected = _md5_hex(raw)
        return secrets.compare_digest(str(expected), str(sign_string or ""))

    async def prepare(self, payload: dict) -> dict:
        click_trans_id = _as_int(payload.get("click_trans_id"))
        service_id = _as_int(payload.get("service_id"))
        click_paydoc_id = _as_int(payload.get("click_paydoc_id"))
        merchant_trans_id = payload.get("merchant_trans_id")
        amount_raw = payload.get("amount")
        action = _as_int(payload.get("action"))
        sign_time = payload.get("sign_time")
        sign_string = payload.get("sign_string")

        if (
            click_trans_id is None
            or service_id is None
            or not merchant_trans_id
            or amount_raw is None
            or action is None
            or not sign_time
            or not sign_string
        ):
            return {
                "error": ClickError.ERROR_IN_REQUEST,
                "error_note": "Error in request from click",
                "click_trans_id": click_trans_id or 0,
                "merchant_trans_id": str(merchant_trans_id or ""),
            }

        if getattr(settings, "CLICK_SERVICE_ID", None) and int(
            settings.CLICK_SERVICE_ID
        ) != int(service_id):
            return {
                "error": ClickError.ERROR_IN_REQUEST,
                "error_note": "Error in request from click",
                "click_trans_id": click_trans_id,
                "merchant_trans_id": str(merchant_trans_id),
            }

        if action != 0:
            return {
                "error": ClickError.ACTION_NOT_FOUND,
                "error_note": "Action not found",
                "click_trans_id": click_trans_id,
                "merchant_trans_id": str(merchant_trans_id),
            }

        if not self._verify_prepare_signature(
            click_trans_id=click_trans_id,
            service_id=service_id,
            merchant_trans_id=merchant_trans_id,
            amount=str(amount_raw),
            action=action,
            sign_time=str(sign_time),
            sign_string=str(sign_string),
        ):
            return {
                "error": ClickError.SIGN_CHECK_FAILED,
                "error_note": "SIGN CHECK FAILED!",
                "click_trans_id": click_trans_id,
                "merchant_trans_id": str(merchant_trans_id),
            }

        invoice = await self.db_handler.find_one(
            self.invoices_collection,
            {
                "$or": [
                    {"order_id": str(merchant_trans_id)},
                    {"invoice_id": str(merchant_trans_id)},
                ]
            },
        )
        if not invoice:
            return {
                "error": ClickError.USER_DOES_NOT_EXIST,
                "error_note": "User does not exist",
                "click_trans_id": click_trans_id,
                "merchant_trans_id": str(merchant_trans_id),
            }

        if invoice.get("provider") != "click":
            return {
                "error": ClickError.USER_DOES_NOT_EXIST,
                "error_note": "User does not exist",
                "click_trans_id": click_trans_id,
                "merchant_trans_id": str(merchant_trans_id),
            }

        if invoice.get("status") == "paid":
            return {
                "error": ClickError.ALREADY_PAID,
                "error_note": "Already paid",
                "click_trans_id": click_trans_id,
                "merchant_trans_id": str(merchant_trans_id),
            }

        if invoice.get("status") != "pending":
            return {
                "error": ClickError.USER_DOES_NOT_EXIST,
                "error_note": "User does not exist",
                "click_trans_id": click_trans_id,
                "merchant_trans_id": str(merchant_trans_id),
            }

        amount_dec = _as_decimal(amount_raw)
        if amount_dec is None:
            return {
                "error": ClickError.INCORRECT_AMOUNT,
                "error_note": "Incorrect parameter amount",
                "click_trans_id": click_trans_id,
                "merchant_trans_id": str(merchant_trans_id),
            }

        expected = Decimal(str(invoice.get("amount_sum") or 0))
        if amount_dec != expected:
            return {
                "error": ClickError.INCORRECT_AMOUNT,
                "error_note": "Incorrect parameter amount",
                "click_trans_id": click_trans_id,
                "merchant_trans_id": str(merchant_trans_id),
            }

        tx = await self.db_handler.find_one(
            self.transactions_collection,
            {"click_trans_id": int(click_trans_id)},
        )
        if tx:
            if tx.get("state") == "paid":
                return {
                    "error": ClickError.ALREADY_PAID,
                    "error_note": "Already paid",
                    "click_trans_id": click_trans_id,
                    "merchant_trans_id": str(merchant_trans_id),
                    "merchant_prepare_id": int(
                        tx.get("merchant_prepare_id") or click_trans_id
                    ),
                }
            if tx.get("state") == "canceled":
                return {
                    "error": ClickError.TRANSACTION_CANCELLED,
                    "error_note": "Transaction cancelled",
                    "click_trans_id": click_trans_id,
                    "merchant_trans_id": str(merchant_trans_id),
                    "merchant_prepare_id": int(
                        tx.get("merchant_prepare_id") or click_trans_id
                    ),
                }

            return {
                "error": ClickError.SUCCESS,
                "error_note": "Success",
                "click_trans_id": click_trans_id,
                "merchant_trans_id": str(merchant_trans_id),
                "merchant_prepare_id": int(
                    tx.get("merchant_prepare_id") or click_trans_id
                ),
            }

        now_ms = int(time.time() * 1000)
        merchant_prepare_id = int(click_trans_id)

        await self.db_handler.insert_one(
            self.transactions_collection,
            {
                "click_trans_id": int(click_trans_id),
                "click_paydoc_id": click_paydoc_id,
                "merchant_trans_id": str(merchant_trans_id),
                "merchant_prepare_id": merchant_prepare_id,
                "amount": str(amount_raw),
                "amount_sum": int(invoice.get("amount_sum") or 0),
                "user_id": invoice.get("user_id"),
                "state": "prepared",
                "provider": "click",
                "create_time": now_ms,
                "perform_time": 0,
                "cancel_time": 0,
            },
        )

        await self.db_handler.update_one(
            self.invoices_collection,
            {
                "$or": [
                    {"order_id": str(merchant_trans_id)},
                    {"invoice_id": str(merchant_trans_id)},
                ]
            },
            {
                "click_trans_id": int(click_trans_id),
                "click_paydoc_id": click_paydoc_id,
                "updated_at": now_ms,
            },
        )

        return {
            "error": ClickError.SUCCESS,
            "error_note": "Success",
            "click_trans_id": click_trans_id,
            "merchant_trans_id": str(merchant_trans_id),
            "merchant_prepare_id": merchant_prepare_id,
        }

    async def _apply_subscription_from_order(
        self, *, order_id: str | None, transaction_id: str | None, now_ms: int
    ) -> None:
        if not order_id:
            return

        invoice = await self.db_handler.find_one(
            self.invoices_collection,
            {"$or": [{"order_id": order_id}, {"invoice_id": order_id}]},
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

        user = await self.db_handler.find_one(self.users_collection, {"_id": user_id})
        user_query = {"_id": user_id}
        if not user:
            user = await self.db_handler.find_one(
                self.users_collection, {"user_id": user_id}
            )
            user_query = {"user_id": user_id}
        if not user:
            return

        sub = user.get("subscription") if isinstance(user, dict) else None
        existing_end = int(sub.get("end_ms") or 0) if isinstance(sub, dict) else 0

        start_ms = max(now_ms, existing_end)
        end_ms = start_ms + int(quote["days"]) * 24 * 60 * 60 * 1000

        subscription_update = {
            "tier": quote["tier"],
            "period": quote["period"],
            "daily_credits": int(quote["daily_credits"]),
            "start_ms": start_ms,
            "end_ms": end_ms,
            "last_order_id": order_id,
            "last_transaction_id": transaction_id,
            "updated_at_ms": now_ms,
        }

        await self.db_handler.update_one(
            self.users_collection, user_query, {"subscription": subscription_update}
        )
        await self.db_handler.update_one(
            self.invoices_collection,
            {"$or": [{"order_id": order_id}, {"invoice_id": order_id}]},
            {"subscription_applied": True, "updated_at": now_ms},
        )

    async def complete(self, payload: dict) -> dict:
        click_trans_id = _as_int(payload.get("click_trans_id"))
        service_id = _as_int(payload.get("service_id"))
        click_paydoc_id = _as_int(payload.get("click_paydoc_id"))
        merchant_trans_id = payload.get("merchant_trans_id")
        merchant_prepare_id = _as_int(payload.get("merchant_prepare_id"))
        amount_raw = payload.get("amount")
        action = _as_int(payload.get("action"))
        error = _as_int(payload.get("error"))
        sign_time = payload.get("sign_time")
        sign_string = payload.get("sign_string")

        if (
            click_trans_id is None
            or service_id is None
            or not merchant_trans_id
            or merchant_prepare_id is None
            or amount_raw is None
            or action is None
            or error is None
            or not sign_time
            or not sign_string
        ):
            return {
                "error": ClickError.ERROR_IN_REQUEST,
                "error_note": "Error in request from click",
                "click_trans_id": click_trans_id or 0,
                "merchant_trans_id": str(merchant_trans_id or ""),
            }

        if getattr(settings, "CLICK_SERVICE_ID", None) and int(
            settings.CLICK_SERVICE_ID
        ) != int(service_id):
            return {
                "error": ClickError.ERROR_IN_REQUEST,
                "error_note": "Error in request from click",
                "click_trans_id": click_trans_id,
                "merchant_trans_id": str(merchant_trans_id),
            }

        if action != 1:
            return {
                "error": ClickError.ACTION_NOT_FOUND,
                "error_note": "Action not found",
                "click_trans_id": click_trans_id,
                "merchant_trans_id": str(merchant_trans_id),
            }

        if not self._verify_complete_signature(
            click_trans_id=click_trans_id,
            service_id=service_id,
            merchant_trans_id=merchant_trans_id,
            merchant_prepare_id=merchant_prepare_id,
            amount=str(amount_raw),
            action=action,
            sign_time=str(sign_time),
            sign_string=str(sign_string),
        ):
            return {
                "error": ClickError.SIGN_CHECK_FAILED,
                "error_note": "SIGN CHECK FAILED!",
                "click_trans_id": click_trans_id,
                "merchant_trans_id": str(merchant_trans_id),
            }

        tx = await self.db_handler.find_one(
            self.transactions_collection,
            {"merchant_prepare_id": int(merchant_prepare_id)},
        )
        if not tx:
            return {
                "error": ClickError.TRANSACTION_DOES_NOT_EXIST,
                "error_note": "Transaction does not exist",
                "click_trans_id": click_trans_id,
                "merchant_trans_id": str(merchant_trans_id),
            }

        if tx.get("state") == "paid":
            return {
                "error": ClickError.ALREADY_PAID,
                "error_note": "Already paid",
                "click_trans_id": click_trans_id,
                "merchant_trans_id": str(merchant_trans_id),
                "merchant_confirm_id": int(tx.get("merchant_confirm_id") or 0) or None,
            }

        if tx.get("state") == "canceled":
            return {
                "error": ClickError.TRANSACTION_CANCELLED,
                "error_note": "Transaction cancelled",
                "click_trans_id": click_trans_id,
                "merchant_trans_id": str(merchant_trans_id),
                "merchant_confirm_id": int(tx.get("merchant_confirm_id") or 0) or None,
            }

        invoice = await self.db_handler.find_one(
            self.invoices_collection,
            {
                "$or": [
                    {"order_id": str(merchant_trans_id)},
                    {"invoice_id": str(merchant_trans_id)},
                ]
            },
        )
        if not invoice or invoice.get("provider") != "click":
            return {
                "error": ClickError.USER_DOES_NOT_EXIST,
                "error_note": "User does not exist",
                "click_trans_id": click_trans_id,
                "merchant_trans_id": str(merchant_trans_id),
            }

        amount_dec = _as_decimal(amount_raw)
        if amount_dec is None:
            return {
                "error": ClickError.INCORRECT_AMOUNT,
                "error_note": "Incorrect parameter amount",
                "click_trans_id": click_trans_id,
                "merchant_trans_id": str(merchant_trans_id),
            }
        expected = Decimal(str(invoice.get("amount_sum") or 0))
        if amount_dec != expected:
            return {
                "error": ClickError.INCORRECT_AMOUNT,
                "error_note": "Incorrect parameter amount",
                "click_trans_id": click_trans_id,
                "merchant_trans_id": str(merchant_trans_id),
            }

        now_ms = int(time.time() * 1000)

        # Click sends negative error on cancellation; vendor must return -9.
        if error != 0:
            await self.db_handler.update_one(
                self.transactions_collection,
                {"merchant_prepare_id": int(merchant_prepare_id)},
                {"state": "canceled", "cancel_time": now_ms, "updated_at": now_ms},
            )
            await self.db_handler.update_one(
                self.invoices_collection,
                {
                    "$or": [
                        {"order_id": str(merchant_trans_id)},
                        {"invoice_id": str(merchant_trans_id)},
                    ]
                },
                {"status": "canceled", "updated_at": now_ms},
            )
            return {
                "error": ClickError.TRANSACTION_CANCELLED,
                "error_note": "Transaction cancelled",
                "click_trans_id": click_trans_id,
                "merchant_trans_id": str(merchant_trans_id),
                "merchant_confirm_id": None,
            }

        merchant_confirm_id = int(click_paydoc_id or click_trans_id)

        await self.db_handler.update_one(
            self.transactions_collection,
            {"merchant_prepare_id": int(merchant_prepare_id)},
            {
                "state": "paid",
                "perform_time": now_ms,
                "merchant_confirm_id": merchant_confirm_id,
                "click_paydoc_id": click_paydoc_id,
                "updated_at": now_ms,
            },
        )

        await self.db_handler.update_one(
            self.invoices_collection,
            {
                "$or": [
                    {"order_id": str(merchant_trans_id)},
                    {"invoice_id": str(merchant_trans_id)},
                ]
            },
            {
                "status": "paid",
                "updated_at": now_ms,
                "click_paydoc_id": click_paydoc_id,
            },
        )

        try:
            await self._apply_subscription_from_order(
                order_id=str(merchant_trans_id),
                transaction_id=str(merchant_confirm_id),
                now_ms=now_ms,
            )
        except Exception as e:
            logger.exception(f"[Click] Failed to apply subscription: {e}")

        return {
            "error": ClickError.SUCCESS,
            "error_note": "Success",
            "click_trans_id": click_trans_id,
            "merchant_trans_id": str(merchant_trans_id),
            "merchant_confirm_id": merchant_confirm_id,
        }
