import base64
import secrets
import time
from math import floor

from app.core.config import settings
from app.core.dependencies import get_mongo_handler
from app.core.logger import logger
from app.models.payme import PaymeData, PaymeError, TransactionError, TransactionState


class TransactionService:
    def __init__(self):
        self.db_handler = get_mongo_handler()
        self.users_collection = settings.USERS_COLLECTION
        self.transaction_collection = settings.TRANSACTION_COLLECTION
        self.invoices_collection = settings.PAYME_INVOICES_COLLECTION
        self.fiscal_collection = settings.PAYME_FISCAL_COLLECTION

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
            # Test plan (for sandbox/testing)
            self._subscription_catalog["test"] = {
                "daily_credits": 70,
                "monthly": {
                    "price_sum": 1000,
                    "days": 30,
                },
                "yearly": {
                    "price_sum": 10000,
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
            "amount_tiyin": price_sum * 100,
        }

    def get_subscription_catalog(self) -> list[dict]:
        """Return subscription plans derived from the configured catalog."""

        plans: list[dict] = []
        for tier, cfg in self._subscription_catalog.items():
            daily = int(cfg.get("daily_credits") or 0)
            for period in ("monthly", "yearly"):
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

        # Stable ordering for clients
        plans.sort(key=lambda p: (p["tier"], p["period"]))
        return plans

    async def get_user_subscription(self, user_id: str) -> dict:
        user = await self.db_handler.find_one(self.users_collection, {"_id": user_id})
        if not user:
            user = await self.db_handler.find_one(
                self.users_collection, {"user_id": user_id}
            )
        if not user:
            return {"user_id": user_id, "active": False}

        sub = user.get("subscription")
        if not isinstance(sub, dict):
            return {"user_id": user_id, "active": False}

        now_ms = int(time.time() * 1000)
        end_ms = int(sub.get("end_ms") or 0)
        active = bool(end_ms > now_ms and (sub.get("daily_credits") or 0) > 0)

        return {
            "user_id": user_id,
            "active": active,
            "tier": sub.get("tier"),
            "period": sub.get("period"),
            "daily_credits": sub.get("daily_credits"),
            "start_ms": sub.get("start_ms"),
            "end_ms": sub.get("end_ms"),
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
        """Create a local invoice (order_id) and return a Payme checkout link.

        This endpoint is meant for our clients. Payme itself will later call Merchant API
        methods (CheckPerformTransaction/CreateTransaction/...) using the encoded account fields.
        """

        quote = None
        if subscription_tier or subscription_period:
            if not (subscription_tier and subscription_period):
                raise ValueError(
                    "subscription_tier and subscription_period are both required"
                )
            quote = self._get_subscription_quote(subscription_tier, subscription_period)
            expected = quote["amount_sum"]
            if amount_sum is not None and amount_sum != expected:
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

        # If this is a subscription init, reuse an existing pending invoice
        # for the same user + tier + period (avoid opening duplicates).
        if quote and not order_id:
            existing_sub_invoice = await self.db_handler.find_one(
                self.invoices_collection,
                {
                    "user_id": user_id,
                    "provider": "payme",
                    "purpose": "subscription",
                    "status": "pending",
                    "subscription.tier": quote.get("tier"),
                    "subscription.period": quote.get("period"),
                },
            )
            if existing_sub_invoice:
                existing_order_id = existing_sub_invoice.get(
                    "order_id"
                ) or existing_sub_invoice.get("invoice_id")
                if existing_order_id:
                    link = await self.create_payment_link(
                        amount=int(
                            existing_sub_invoice.get("amount_sum") or amount_sum
                        ),
                        user_id=user_id,
                        callback_url=str(
                            existing_sub_invoice.get("callback_url") or callback_url
                        ),
                        order_id=str(existing_order_id),
                    )
                    return {"order_id": str(existing_order_id), "link": link}

        order_id_value = order_id or secrets.token_hex(8)
        amount_tiyin = amount_sum * 100

        existing_invoice = await self.db_handler.find_one(
            self.invoices_collection,
            {"$or": [{"order_id": order_id_value}, {"invoice_id": order_id_value}]},
        )
        if existing_invoice:
            # If the client re-uses order_id for the same pending invoice, return the same link.
            if (
                existing_invoice.get("user_id") == user_id
                and existing_invoice.get("status") == "pending"
                and existing_invoice.get("provider") == "payme"
            ):
                link = await self.create_payment_link(
                    amount=int(existing_invoice.get("amount_sum") or amount_sum),
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
            {
                "order_id": order_id_value,
                "user_id": user_id,
                "amount_sum": amount_sum,
                "amount_tiyin": amount_tiyin,
                "callback_url": callback_url,
                "status": "pending",
                "provider": "payme",
                "purpose": "subscription" if quote else "payment",
                "subscription": quote,
                "subscription_applied": False,
                "requisites": {
                    "user_id": user_id,
                    "order_id": order_id_value,
                },
                "created_at": now_ms,
                "updated_at": now_ms,
            },
        )

        link = await self.create_payment_link(
            amount=amount_sum,
            user_id=user_id,
            callback_url=callback_url,
            order_id=order_id_value,
        )

        return {"order_id": order_id_value, "link": link}

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
        if not quote:
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
            "provider": "payme",
        }

        logger.info(f"Creating new transaction: {new_transaction}")

        await self.db_handler.insert_one(self.transaction_collection, new_transaction)

        # Link transaction to invoice for easier reconciliation
        await self.db_handler.update_one(
            self.invoices_collection,
            {
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
        user = await self.db_handler.find_one(
            self.users_collection, {"user_id": user_id}
        )
        if not user:
            user = await self.db_handler.find_one(
                self.users_collection, {"_id": user_id}
            )
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
