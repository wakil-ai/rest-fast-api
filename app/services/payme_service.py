from bson import ObjectId
from motor.motor_asyncio import AsyncIOMotorCollection
from app.db.mongo_handler import MongoHandler
from app.models.payme import PaymeError, PaymeData, TransactionState, TransactionError, TransactionModel
import time
from math import floor

class TransactionService:
    def __init__(self):
        self.db_handler = MongoHandler()
        self.users_collection = 'users'
        self.transaction_collection = 'transactions'
        
    async def check_perform_transaction(self, params, request_id):
        account = params["account"]
        amount = params["amount"]

        amount = floor(amount / 100)

        user = self.db_handler.find_documents(self.users_collection, {"user_id": account["user_id"]})
        if not user:
            raise TransactionError(PaymeError.UserNotFound, request_id, PaymeData.UserId)

    
    async def check_transaction(self, params, request_id):
        transaction = self.db_handler.find_one(self.transaction_collection, {"id": params["id"]})
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

        await self.check_perform_transaction(params, request_id)

        transaction = self.db_handler.find_one(self.transaction_collection, {"id": params["id"]})
        current_time = int(time.time() * 1000)

        if transaction:
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

            return {
                "create_time": transaction["create_time"],
                "transaction": transaction["id"],
                "state": TransactionState.Pending,
            }

        existing = self.db_handler.find_one(self.transaction_collection, {
            "user": account["user_id"],
            "provider": "payme",
        })

        if existing:
            if existing["state"] == TransactionState.Paid:
                raise TransactionError(PaymeError.AlreadyDone, request_id)
            if existing["state"] == TransactionState.Pending:
                raise TransactionError(PaymeError.Pending, request_id)

        new_transaction = {
            "id": params["id"],
            "state": TransactionState.Pending,
            "amount": amount,
            "user": account["user_id"],
            "create_time": time_ms,
            "provider": "payme",
        }

        self.db_handler.insert_one(self.transaction_collection, new_transaction)

        return {
            "transaction": params["id"],
            "state": TransactionState.Pending,
            "create_time": time_ms,
        }

    async def perform_transaction(self, params, request_id):
        current_time = int(time.time() * 1000)

        transaction = self.db_handler.find_one(self.transaction_collection, {"id": params["id"]})
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
                "reason": transaction.get("reason")
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
            }
        )
        
        # Return complete transaction details
        return {
            "create_time": transaction["create_time"],
            "perform_time": current_time,
            "cancel_time": 0,
            "transaction": transaction["id"],
            "state": TransactionState.Paid,
            "reason": None
        }


    async def cancel_transaction(self, params, request_id):
        transaction = self.db_handler.find_one(self.transaction_collection, {"id": params["id"]})
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
        cursor = self.db_handler.db[self.transaction_collection].find({
            "create_time": {
                "$gte": params["from"],
                "$lte": params["to"],
            }
        }).sort("create_time", 1).limit(params.get("limit", 100))

        result = []
        for tx in cursor:  # Regular for loop (MongoHandler is synchronous)
            result.append({
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
            })

        return result
