import base64
import time
from math import floor

from app.core.config import settings
from app.db.mongo_handler import MongoHandler
from app.models.payme import PaymeData, PaymeError, TransactionError, TransactionState


class TransactionService:
    def __init__(self):
        self.db_handler = MongoHandler()
        self.users_collection = "users"
        self.transaction_collection = "transactions"

    async def check_perform_transaction(self, params, request_id):
        # Validate account parameter exists
        if "account" not in params:
            raise TransactionError(
                PaymeError.UserNotFound, request_id, PaymeData.UserId
            )

        account = params["account"]

        # Validate user_id in account
        if "user_id" not in account or not account["user_id"]:
            raise TransactionError(
                PaymeError.UserNotFound, request_id, PaymeData.UserId
            )

        # Validate order_id in account
        if (
            "order_id" not in account
            or not account["order_id"]
        ):
            raise TransactionError(PaymeError.UserNotFound, request_id, "order_id")

        # Validate amount parameter exists and is valid
        if "amount" not in params:
            raise TransactionError(PaymeError.InvalidAmount, request_id)

        amount = params["amount"]

        # Amount must be positive integer
        if not isinstance(amount, (int, float)) or amount <= 0:
            raise TransactionError(PaymeError.InvalidAmount, request_id)

        # Convert from tiyin to sum (divide by 100)
        amount = floor(amount / 100)

        # Only allow specific payment amounts: 1000, 5000, or 15000 sum
        if isinstance(amount, int) and amount > 0:
            raise TransactionError(PaymeError.InvalidAmount, request_id)

        # Check if user exists
        user = self.db_handler.find_documents(
            self.users_collection, {"user_id": account["user_id"]}
        )
        if not user:
            raise TransactionError(
                PaymeError.UserNotFound, request_id, PaymeData.UserId
            )

    async def check_transaction(self, params, request_id):
        transaction = self.db_handler.find_one(
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
                self.db_handler.update_one(
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

        # Primary check: Search by transaction ID, user_id, and order_id
        transaction = self.db_handler.find_one(
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
                    self.db_handler.update_one(
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

        existing_tx = self.db_handler.find_one(
            self.transaction_collection,
            {
                "user": account["user_id"],
                "order_id": account["order_id"],
            },
        )
        if existing_tx:
            if existing_tx["state"] == TransactionState.Pending:
                raise TransactionError(PaymeError.Pending, request_id)

        # Create new transaction - transaction ID is the primary key
        new_transaction = {
            "id": transaction_id,
            "state": TransactionState.Pending,
            "amount": amount,
            "user": account["user_id"],
            "order_id": account["order_id"],
            "create_time": time_ms,
            "provider": "payme",
        }

        self.db_handler.insert_one(self.transaction_collection, new_transaction)

        return {
            "transaction": transaction_id,
            "state": TransactionState.Pending,
            "create_time": time_ms,
        }

    async def perform_transaction(self, params, request_id):
        current_time = int(time.time() * 1000)

        transaction = self.db_handler.find_one(
            self.transaction_collection, {"id": params["id"]}
        )
        if not transaction:
            raise TransactionError(PaymeError.TransactionNotFound, request_id)

        # If already paid, return existing perform_time (IDEMPOTENT)
        if transaction["state"] == TransactionState.Paid:
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
            self.db_handler.update_one(
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
        self.db_handler.update_one(
            self.transaction_collection,
            {"id": params["id"]},
            {
                "state": TransactionState.Paid,
                "perform_time": current_time,
            },
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
        transaction = self.db_handler.find_one(
            self.transaction_collection, {"id": params["id"]}
        )
        if not transaction:
            raise TransactionError(PaymeError.TransactionNotFound, request_id)

        current_time = int(time.time() * 1000)

        if transaction["state"] > 0:
            self.db_handler.update_one(
                self.transaction_collection,
                {"id": params["id"]},
                {
                    "state": -abs(transaction["state"]),
                    "cancel_time": current_time,
                    "reason": params.get("reason", 0),
                },
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
        for tx in cursor:  # Regular for loop (MongoHandler is synchronous)
            result.append(
                {
                    "id": tx["id"],
                    "time": tx["create_time"],
                    "amount": tx["amount"] * 100,  # Convert sum to tiyin
                    "account": {
                        "user_id": tx["user"],
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
        self, amount: int, user_id: str, callback_url: str
    ) -> str:
        """
        Create a Payme payment link for the specified amount and user.
        """
        user = self.db_handler.find_one(self.users_collection, {"user_id": user_id})
        if not user:
            raise ValueError("User not found")

        amount = amount * 100  # Convert to tiyin (smallest currency unit)

        raw_string = f"m={settings.PAYME_MERCHANT_ID};ac.user_id={user_id};a={amount};c={callback_url};"

        encoded = base64.b64encode(raw_string.encode()).decode()

        return f"{settings.PAYME_PAYMENT_LINK_BASE}{encoded}"

    async def set_fiscal_data(self, params, request_id):
        """
        Save fiscal data for a transaction.
        Payme sends fiscal receipt data after successful payment or cancellation.
        """
        transaction_id = params.get("id")
        fiscal_type = params.get("type")  # "PERFORM" or "CANCEL"
        fiscal_data = params.get("fiscal_data")

        # Validate required parameters
        if not transaction_id:
            raise TransactionError(PaymeError.InvalidParams, request_id)

        if not fiscal_type or fiscal_type not in ["PERFORM", "CANCEL"]:
            raise TransactionError(PaymeError.InvalidParams, request_id)

        if not fiscal_data:
            raise TransactionError(PaymeError.InvalidParams, request_id)

        # Find transaction
        transaction = self.db_handler.find_one(
            self.transaction_collection, {"id": transaction_id}
        )

        if not transaction:
            raise TransactionError(PaymeError.FiscalReceiptNotFound, request_id)

        # Prepare fiscal data update
        fiscal_field = (
            "fiscal_perform_data" if fiscal_type == "PERFORM" else "fiscal_cancel_data"
        )

        # Update transaction with fiscal data
        self.db_handler.update_one(
            self.transaction_collection,
            {"id": transaction_id},
            {fiscal_field: fiscal_data},
        )

        return {"success": True}
