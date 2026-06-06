import secrets
import time
from decimal import Decimal
from urllib.parse import urlencode

from app.core.config import settings
from app.core.logger import logger
from app.models.payment import ClickError
from app.services.payments.base import BasePaymentService
from app.utils.text_cleaning import _as_decimal, _as_int, _md5_hex


class ClickService(BasePaymentService):
    provider = "click"

    def __init__(self):
        super().__init__()
        self.transactions_collection = settings.CLICK_TRANSACTIONS_COLLECTION

    async def build_payment_link(
        self, *, amount_sum: int, user_id: str, callback_url: str, order_id: str
    ) -> str:
        return self.create_payment_link(
            amount_sum=amount_sum,
            order_id=order_id,
            callback_url=callback_url,
        )

    def create_payment_link(
        self, *, amount_sum: int, order_id: str, callback_url: str
    ) -> str:
        merchant_id = settings.CLICK_MERCHANT_ID
        service_id = settings.CLICK_SERVICE_ID
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
        merchant_user_id = settings.CLICK_MERCHANT_USER_ID
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
        secret_key = settings.CLICK_SECRET_KEY
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
        secret_key = settings.CLICK_SECRET_KEY
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

        if settings.CLICK_SERVICE_ID and int(settings.CLICK_SERVICE_ID) != int(
            service_id
        ):
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
                "provider": self.provider,
                "$or": [
                    {"order_id": str(merchant_trans_id)},
                    {"invoice_id": str(merchant_trans_id)},
                ],
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
                "provider": self.provider,
                "create_time": now_ms,
                "perform_time": 0,
                "cancel_time": 0,
            },
        )

        await self.db_handler.update_one(
            self.invoices_collection,
            {
                "provider": self.provider,
                "$or": [
                    {"order_id": str(merchant_trans_id)},
                    {"invoice_id": str(merchant_trans_id)},
                ],
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
        await self._finalize_subscription_invoice(
            order_id=order_id,
            transaction_id=transaction_id,
            now_ms=now_ms,
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

        if settings.CLICK_SERVICE_ID and int(settings.CLICK_SERVICE_ID) != int(
            service_id
        ):
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
                "provider": self.provider,
                "$or": [
                    {"order_id": str(merchant_trans_id)},
                    {"invoice_id": str(merchant_trans_id)},
                ],
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
                    "provider": self.provider,
                    "$or": [
                        {"order_id": str(merchant_trans_id)},
                        {"invoice_id": str(merchant_trans_id)},
                    ],
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
                "provider": self.provider,
                "$or": [
                    {"order_id": str(merchant_trans_id)},
                    {"invoice_id": str(merchant_trans_id)},
                ],
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
