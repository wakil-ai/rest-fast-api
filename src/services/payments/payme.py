import base64
import time
from math import floor

from core.config import settings
from core.logger import logger
from models.payment import PaymeData, PaymeError, TransactionError, TransactionState
from services.payments.base import BasePaymentService


class TransactionService(BasePaymentService):  # Paycom
    provider = "payme"

    def __init__(self):
        super().__init__()
        self.transaction_collection = settings.TRANSACTION_COLLECTION
        self.fiscal_collection = settings.PAYME_FISCAL_COLLECTION

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
        document = super()._build_invoice_document(
            order_id=order_id,
            user_id=user_id,
            amount_sum=amount_sum,
            callback_url=callback_url,
            purpose=purpose,
            quote=quote,
            now_ms=now_ms,
        )
        document.update(
            {
                "amount_tiyin": amount_sum * 100,
                "requisites": {"user_id": user_id, "order_id": order_id},
            }
        )
        return document

    async def build_payment_link(
        self, *, amount_sum: int, user_id: str, callback_url: str, order_id: str
    ) -> str:
        return await self.create_payment_link(
            amount=amount_sum,
            user_id=user_id,
            callback_url=callback_url,
            order_id=order_id,
        )

    async def _apply_subscription_from_order(
        self, *, order_id: str | None, transaction_id: str | None, now_ms: int
    ) -> None:
        await self._finalize_subscription_invoice(
            order_id=order_id,
            transaction_id=transaction_id,
            now_ms=now_ms,
        )

    async def check_perform_transaction(self, params, request_id):
        if not params or "account" not in params:
            raise TransactionError(PaymeError.UserNotFound, request_id, "account")

        account = params["account"] or {}

        user_id = account.get("user_id")
        if not user_id:
            raise TransactionError(
                PaymeError.UserNotFound, request_id, PaymeData.UserId
            )

        order_id = account.get("order_id")
        if not order_id:
            raise TransactionError(PaymeError.UserNotFound, request_id, "order_id")

        if "amount" not in params:
            raise TransactionError(PaymeError.InvalidAmount, request_id)

        amount_tiyin = params["amount"]
        if not isinstance(amount_tiyin, int) or amount_tiyin <= 0:
            raise TransactionError(PaymeError.InvalidAmount, request_id)

        invoice = await self.db_handler.find_one(
            self.invoices_collection,
            {
                "provider": self.provider,
                "$or": [{"order_id": order_id}, {"invoice_id": order_id}],
                "user_id": user_id,
                "status": "pending",
            },
        )
        if not invoice:
            raise TransactionError(PaymeError.UserNotFound, request_id, "order_id")

        if invoice.get("amount_tiyin") != amount_tiyin:
            raise TransactionError(PaymeError.InvalidAmount, request_id)

        user = await self.db_handler.find_one(
            self.users_collection, {"user_id": user_id}
        )
        if not user:
            user = await self.db_handler.find_one(
                self.users_collection, {"_id": user_id}
            )
        if not user:
            raise TransactionError(
                PaymeError.UserNotFound, request_id, PaymeData.UserId
            )

        if (
            not settings.PAYME_FISCAL_IKPU_CODE
            and not settings.PAYME_FISCAL_PACKAGE_CODE
        ):
            logger.error(
                "Payme fiscalization IKPU code and package code are not set. Fiscal details will be empty."
            )
            raise TransactionError(
                PaymeError.CantDoOperation,
                request_id,
                "Fiscalization details are not configured",
            )

        return {
            "allow": True,
            "additional": {
                "order_id": order_id,
                "user_id": user_id,
            },
            "detail": {
                "receipt_type": settings.PAYME_FISCAL_RECEIPT_TYPE,
                "items": [
                    {
                        "title": settings.PAYME_FISCAL_RECEIPT_TITLE,
                        "price": int(amount_tiyin),
                        "count": 1,
                        "code": settings.PAYME_FISCAL_IKPU_CODE,
                        "vat_percent": settings.PAYME_FISCAL_VAT_PERCENT,
                        "package_code": settings.PAYME_FISCAL_PACKAGE_CODE,
                    }
                ],
            },
        }

    async def check_transaction(self, params, request_id):
        transaction = await self.db_handler.find_one(
            self.transaction_collection, {"id": params["id"]}
        )
        if not transaction:
            raise TransactionError(PaymeError.TransactionNotFound, request_id)

        # If transaction is pending, check for timeout (12 hours = 43,200,000 ms)
        if transaction["state"] == TransactionState.Pending:
            current_time = int(time.time() * 1000)
            time_diff_ms = current_time - transaction["create_time"]

            # If timeout exceeded, cancel with reason 4 (ONE TIME ONLY)
            if time_diff_ms >= 43200000:
                await self.db_handler.update_one(
                    self.transaction_collection,
                    {"id": params["id"]},
                    {
                        "state": TransactionState.PendingCanceled,
                        "reason": 4,
                        "cancel_time": current_time,
                    },
                )
                # Update local transaction object to return consistent state
                transaction["state"] = TransactionState.PendingCanceled
                transaction["reason"] = 4
                transaction["cancel_time"] = current_time

        return {
            "create_time": transaction.get("create_time"),
            "perform_time": transaction.get("perform_time", 0),
            "cancel_time": transaction.get("cancel_time", 0),
            "transaction": transaction["id"],
            "state": transaction["state"],
            "reason": transaction.get("reason"),
        }

    async def create_transaction(self, params, request_id):
        account = params["account"]
        amount = floor(params["amount"] / 100)
        time_ms = params["time"]
        transaction_id = params["id"]

        txn_user_id = account.get("user_id")
        txn_order_id = account.get("order_id")
        txn_subscription_type = None
        txn_duration = None

        # Primary check: Search by transaction ID, user_id, and order_id
        transaction = await self.db_handler.find_one(
            self.transaction_collection,
            {
                "id": transaction_id,
            },
        )
        current_time = int(time.time() * 1000)

        # If transaction exists with this ID (idempotent call)
        if transaction:
            # If state is Pending, return it (idempotent)
            if transaction["state"] == TransactionState.Pending:
                # Check timeout: 12 hours = 43,200,000 milliseconds
                time_diff_ms = current_time - transaction["create_time"]
                if time_diff_ms >= 43200000:
                    await self.db_handler.update_one(
                        self.transaction_collection,
                        {"id": transaction_id},
                        {
                            "state": TransactionState.PendingCanceled,
                            "reason": 4,
                            "cancel_time": current_time,
                        },
                    )
                    raise TransactionError(PaymeError.CantDoOperation, request_id)

                return {
                    "create_time": transaction["create_time"],
                    "transaction": transaction["id"],
                    "state": TransactionState.Pending,
                }
            else:
                # Transaction exists but not in Pending state
                raise TransactionError(PaymeError.CantDoOperation, request_id)

        await self.check_perform_transaction(params, request_id)

        existing_tx = await self.db_handler.find_one(
            self.transaction_collection,
            {
                "order_id": txn_order_id,
                "$or": [
                    {"user_id": txn_user_id},
                    {"user": txn_user_id},
                ],
            },
        )
        if existing_tx:
            if existing_tx["state"] == TransactionState.Pending:
                raise TransactionError(PaymeError.Pending, request_id)

        # Store subscription metadata in DB based on our invoice.
        # Payme requisites/account fields remain only {user_id, order_id}.
        invoice = await self.db_handler.find_one(
            self.invoices_collection,
            {
                "provider": self.provider,
                "$or": [{"order_id": txn_order_id}, {"invoice_id": txn_order_id}],
                "user_id": txn_user_id,
            },
        )
        invoice_quote = invoice.get("subscription") if invoice else None
        if invoice_quote:
            txn_subscription_type = invoice_quote.get("tier")
            txn_duration = invoice_quote.get("period")

        # Create new transaction - transaction ID is the primary key
        new_transaction = {
            "id": transaction_id,
            "state": TransactionState.Pending,
            "amount": amount,
            "user_id": txn_user_id,
            "order_id": txn_order_id,
            "subscription_type": txn_subscription_type,
            "duration": txn_duration,
            "create_time": time_ms,
            "provider": self.provider,
        }

        logger.info(f"Creating new transaction: {new_transaction}")

        await self.db_handler.insert_one(self.transaction_collection, new_transaction)

        # Link transaction to invoice for easier reconciliation
        await self.db_handler.update_one(
            self.invoices_collection,
            {
                "provider": self.provider,
                "$or": [{"order_id": txn_order_id}, {"invoice_id": txn_order_id}],
                "user_id": txn_user_id,
            },
            {
                "payme_transaction_id": transaction_id,
                "updated_at": int(time.time() * 1000),
            },
        )

        return {
            "transaction": transaction_id,
            "state": TransactionState.Pending,
            "create_time": time_ms,
        }

    async def perform_transaction(self, params, request_id):
        current_time = int(time.time() * 1000)

        transaction = await self.db_handler.find_one(
            self.transaction_collection, {"id": params["id"]}
        )
        if not transaction:
            raise TransactionError(PaymeError.TransactionNotFound, request_id)

        # If already paid, return existing perform_time (IDEMPOTENT)
        if transaction["state"] == TransactionState.Paid:
            await self._apply_subscription_from_order(
                order_id=transaction.get("order_id"),
                transaction_id=transaction.get("id"),
                now_ms=current_time,
            )
            return {
                "create_time": transaction["create_time"],
                "perform_time": transaction.get("perform_time", 0),
                "cancel_time": transaction.get("cancel_time", 0),
                "transaction": transaction["id"],
                "state": TransactionState.Paid,
                "reason": transaction.get("reason"),
            }

        # If not pending, cannot perform
        if transaction["state"] != TransactionState.Pending:
            raise TransactionError(PaymeError.CantDoOperation, request_id)

        # Check timeout: 12 hours = 43,200,000 milliseconds
        time_diff_ms = current_time - transaction["create_time"]
        if time_diff_ms >= 43200000:
            await self.db_handler.update_one(
                self.transaction_collection,
                {"id": params["id"]},
                {
                    "state": TransactionState.PendingCanceled,
                    "reason": 4,
                    "cancel_time": current_time,
                },
            )
            raise TransactionError(PaymeError.CantDoOperation, request_id)

        # Perform the transaction
        await self.db_handler.update_one(
            self.transaction_collection,
            {"id": params["id"]},
            {
                "state": TransactionState.Paid,
                "perform_time": current_time,
            },
        )

        await self.db_handler.update_one(
            self.invoices_collection,
            {
                "provider": self.provider,
                "$or": [
                    {"order_id": transaction.get("order_id")},
                    {"invoice_id": transaction.get("order_id")},
                ],
                "user_id": transaction.get("user_id") or transaction.get("user"),
            },
            {"status": "paid", "updated_at": current_time},
        )

        await self._apply_subscription_from_order(
            order_id=transaction.get("order_id"),
            transaction_id=transaction.get("id"),
            now_ms=current_time,
        )

        # Return complete transaction details
        return {
            "create_time": transaction["create_time"],
            "perform_time": current_time,
            "cancel_time": 0,
            "transaction": transaction["id"],
            "state": TransactionState.Paid,
            "reason": None,
        }

    async def cancel_transaction(self, params, request_id):
        transaction = await self.db_handler.find_one(
            self.transaction_collection, {"id": params["id"]}
        )
        if not transaction:
            raise TransactionError(PaymeError.TransactionNotFound, request_id)

        current_time = int(time.time() * 1000)

        if transaction["state"] > 0:
            await self.db_handler.update_one(
                self.transaction_collection,
                {"id": params["id"]},
                {
                    "state": -abs(transaction["state"]),
                    "cancel_time": current_time,
                    "reason": params.get("reason", 0),
                },
            )

            await self.db_handler.update_one(
                self.invoices_collection,
                {
                    "provider": self.provider,
                    "$or": [
                        {"order_id": transaction.get("order_id")},
                        {"invoice_id": transaction.get("order_id")},
                    ],
                    "user_id": transaction.get("user_id") or transaction.get("user"),
                },
                {"status": "canceled", "updated_at": current_time},
            )

        return {
            "cancel_time": transaction.get("cancel_time", current_time),
            "transaction": transaction["id"],
            "state": -abs(transaction["state"]),
        }

    async def get_statement(self, params):
        cursor = (
            self.db_handler.db[self.transaction_collection]
            .find(
                {
                    "create_time": {
                        "$gte": params["from"],
                        "$lte": params["to"],
                    }
                }
            )
            .sort("create_time", 1)
            .limit(params.get("limit", 100))
        )

        result = []
        async for tx in cursor:
            result.append(
                {
                    "id": tx["id"],
                    "time": tx["create_time"],
                    "amount": tx["amount"] * 100,
                    "account": {
                        "user_id": tx.get("user_id") or tx.get("user"),
                        "order_id": tx.get("order_id"),
                    },
                    "create_time": tx["create_time"],
                    "perform_time": tx.get("perform_time", 0),
                    "cancel_time": tx.get("cancel_time", 0),
                    "transaction": tx["id"],
                    "state": tx["state"],
                    "reason": tx.get("reason"),
                }
            )

        return result

    async def create_payment_link(
        self,
        amount: int,
        user_id: str,
        callback_url: str,
        order_id: str | None = None,
    ) -> str:
        """
        Create a Payme payment link for the specified amount and user.

        `amount` is in SUM; Payme checkout link requires amount in TIYIN.
        """
        user = await self.get_user_by_id(user_id)
        if not user:
            raise ValueError("User not found")

        amount = amount * 100  # Convert to tiyin (smallest currency unit)

        raw_string = f"m={settings.PAYME_MERCHANT_ID};ac.user_id={user_id};"
        if order_id:
            raw_string += f"ac.order_id={order_id};"
        raw_string += f"a={amount};c={callback_url};"

        encoded = base64.b64encode(raw_string.encode()).decode()

        return f"{settings.PAYME_PAYMENT_LINK_BASE}{encoded}"

    async def set_fiscal_data(self, params, request_id):
        """
        Save fiscal data for a transaction.
        Payme sends fiscal receipt data after successful payment or cancellation.
        """
        # Per Payme docs this method is optional, but Payme may still call it.
        # Never fail the payment flow due to fiscal data delivery; ACK success.
        try:
            params = params or {}
            check_id = params.get("id")
            fiscal_type = params.get("type") or "PERFORM"
            fiscal_data = params.get("fiscal_data") or {}

            if fiscal_type not in ["PERFORM", "CANCEL"]:
                fiscal_type = "PERFORM"

            if check_id:
                collection = self.db_handler.db[self.fiscal_collection]
                now_ms = int(time.time() * 1000)
                await collection.update_one(
                    {"check_id": check_id, "type": fiscal_type},
                    {
                        "$set": {
                            "check_id": check_id,
                            "type": fiscal_type,
                            "fiscal_data": fiscal_data,
                            "updated_at": now_ms,
                            "request_id": request_id,
                        },
                        "$setOnInsert": {"created_at": now_ms},
                    },
                    upsert=True,
                )
        except Exception:
            pass

        return {"success": True}


PaymeService = TransactionService
