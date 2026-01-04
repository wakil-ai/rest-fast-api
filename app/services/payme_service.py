"""Payme payment service"""

import time
import base64
from typing import Dict, Optional, List
from app.models.payme import (
    PaymeParams,
    PaymeTransaction,
    TransactionState,
    PaymeTransactionResponse,
    PaymeCheckPerformTransactionResponse,
    PaymePaymentLink,
    PaymePaymentLinkResponse
)
from app.core.config import settings
from app.core.logger import logger
from app.db.mongo_handler import MongoHandler


class PaymeError(Exception):
    """Base Payme error"""
    def __init__(self, code: int, message: Dict[str, str], data: Optional[str] = None, request_id: Optional[int] = None):
        self.code = code
        self.message = message
        self.data = data
        self.request_id = request_id
        super().__init__(message.get("en", "Unknown error"))


class PaymeService:
    """Service for handling Payme payment operations"""
    
    # Error codes according to Payme documentation
    ERRORS = {
        "InvalidAmount": {
            "code": -31001,
            "message": {
                "uz": "Noto'g'ri summa",
                "ru": "Недопустимая сумма",
                "en": "Invalid amount"
            }
        },
        "UserNotFound": {
            "code": -31050,
            "message": {
                "uz": "Biz sizning hisobingizni topolmadik.",
                "ru": "Мы не нашли вашу учетную запись",
                "en": "We couldn't find your account"
            }
        },
        "CantDoOperation": {
            "code": -31008,
            "message": {
                "uz": "Biz operatsiyani bajara olmaymiz",
                "ru": "Мы не можем сделать операцию",
                "en": "We can't do operation"
            }
        },
        "TransactionNotFound": {
            "code": -31003,
            "message": {
                "uz": "Tranzaksiya topilmadi",
                "ru": "Транзакция не найдена",
                "en": "Transaction not found"
            }
        },
        "AlreadyDone": {
            "code": -31060,
            "message": {
                "uz": "Buyurtma to'langan",
                "ru": "Заказ оплачен",
                "en": "Order already paid"
            }
        },
        "Pending": {
            "code": -31050,
            "message": {
                "uz": "Buyurtma to'lovni kutmoqda",
                "ru": "Заказ ожидает оплаты",
                "en": "Order is pending payment"
            }
        },
        "InvalidAuthorization": {
            "code": -32504,
            "message": {
                "uz": "Avtorizatsiya xatosi",
                "ru": "Ошибка авторизации",
                "en": "Authorization error"
            }
        }
    }
    
    def __init__(self):
        self.db = MongoHandler()
        self.collection_name = "payme_transactions"
    
    def check_perform_transaction(self, params: PaymeParams, request_id: int) -> PaymeCheckPerformTransactionResponse:
        """
        Check if transaction can be performed
        This is called before creating a transaction
        """
        logger.info(f"CheckPerformTransaction: {params}")
        
        # Validate amount
        if not params.amount or params.amount <= 0:
            raise PaymeError(**self.ERRORS["InvalidAmount"], request_id=request_id)
        
        # Convert from tiyin to sum
        amount_sum = params.amount // 100
        
        # Validate user exists
        user_id = params.account.user_id
        # Here you would check if user exists in your system
        # For now, we'll assume user exists if user_id is provided
        if not user_id:
            raise PaymeError(**self.ERRORS["UserNotFound"], request_id=request_id)
        
        logger.info(f"Transaction can be performed for user {user_id}, amount: {amount_sum} sum")
        return PaymeCheckPerformTransactionResponse(allow=True)
    
    def check_transaction(self, params: PaymeParams, request_id: int) -> PaymeTransactionResponse:
        """Check transaction status"""
        logger.info(f"CheckTransaction: {params}")
        
        transaction = self.db.find_one(
            self.collection_name,
            {"id": params.id}
        )
        
        if not transaction:
            raise PaymeError(**self.ERRORS["TransactionNotFound"], request_id=request_id)
        
        return PaymeTransactionResponse(
            create_time=transaction["create_time"],
            perform_time=transaction.get("perform_time", 0),
            cancel_time=transaction.get("cancel_time", 0),
            transaction=transaction["id"],
            state=transaction["state"],
            reason=transaction.get("reason")
        )
    
    def create_transaction(self, params: PaymeParams, request_id: int) -> PaymeTransactionResponse:
        """Create a new transaction"""
        logger.info(f"CreateTransaction: {params}")
        
        # Check if transaction can be performed
        self.check_perform_transaction(params, request_id)
        
        # Convert amount from tiyin to sum
        amount_sum = params.amount // 100
        
        # Check if transaction already exists
        existing_transaction = self.db.find_one(
            self.collection_name,
            {"id": params.id}
        )
        
        if existing_transaction:
            if existing_transaction["state"] != TransactionState.PENDING:
                raise PaymeError(**self.ERRORS["CantDoOperation"], request_id=request_id)
            
            # Check if transaction is not expired (12 minutes)
            current_time = int(time.time() * 1000)
            expiration_time = (current_time - existing_transaction["create_time"]) / 60000 < 12
            
            if not expiration_time:
                # Cancel expired transaction
                self.db.update_one(
                    self.collection_name,
                    {"id": params.id},
                    {
                        "state": TransactionState.PENDING_CANCELED,
                        "reason": 4,
                        "cancel_time": current_time
                    }
                )
                raise PaymeError(**self.ERRORS["CantDoOperation"], request_id=request_id)
            
            return PaymeTransactionResponse(
                create_time=existing_transaction["create_time"],
                perform_time=0,
                cancel_time=0,
                transaction=existing_transaction["id"],
                state=TransactionState.PENDING
            )
        
        # Check for duplicate order
        duplicate = self.db.find_one(
            self.collection_name,
            {
                "user_id": params.account.user_id,
                "order_id": params.account.order_id,
                "provider": "payme"
            }
        )
        
        if duplicate:
            if duplicate["state"] == TransactionState.PAID:
                raise PaymeError(**self.ERRORS["AlreadyDone"], request_id=request_id)
            if duplicate["state"] == TransactionState.PENDING:
                raise PaymeError(**self.ERRORS["Pending"], request_id=request_id)
        
        # Create new transaction
        new_transaction = PaymeTransaction(
            id=params.id,
            state=TransactionState.PENDING,
            amount=amount_sum,
            user_id=params.account.user_id,
            order_id=params.account.order_id,
            create_time=params.time,
            provider="payme"
        )
        
        self.db.insert_one(
            self.collection_name,
            new_transaction.model_dump()
        )
        
        logger.info(f"Transaction created: {new_transaction.id}")
        
        return PaymeTransactionResponse(
            transaction=new_transaction.id,
            state=TransactionState.PENDING,
            create_time=new_transaction.create_time,
            perform_time=0,
            cancel_time=0
        )
    
    def perform_transaction(self, params: PaymeParams, request_id: int) -> PaymeTransactionResponse:
        """Perform (complete) transaction"""
        logger.info(f"PerformTransaction: {params}")
        
        current_time = int(time.time() * 1000)
        
        transaction = self.db.find_one(
            self.collection_name,
            {"id": params.id}
        )
        
        if not transaction:
            raise PaymeError(**self.ERRORS["TransactionNotFound"], request_id=request_id)
        
        if transaction["state"] != TransactionState.PENDING:
            if transaction["state"] != TransactionState.PAID:
                raise PaymeError(**self.ERRORS["CantDoOperation"], request_id=request_id)
            
            # Already paid, return existing response
            return PaymeTransactionResponse(
                perform_time=transaction["perform_time"],
                transaction=transaction["id"],
                state=TransactionState.PAID,
                create_time=transaction["create_time"],
                cancel_time=0
            )
        
        # Check if transaction is not expired (12 minutes)
        expiration_time = (current_time - transaction["create_time"]) / 60000 < 12
        
        if not expiration_time:
            # Cancel expired transaction
            self.db.update_one(
                self.collection_name,
                {"id": params.id},
                {
                    "state": TransactionState.PENDING_CANCELED,
                    "reason": 4,
                    "cancel_time": current_time
                }
            )
            raise PaymeError(**self.ERRORS["CantDoOperation"], request_id=request_id)
        
        # Perform transaction
        self.db.update_one(
            self.collection_name,
            {"id": params.id},
            {
                "state": TransactionState.PAID,
                "perform_time": current_time
            }
        )
        
        logger.info(f"Transaction performed: {params.id}")
        
        # Here you would add credits to user or perform other business logic
        # await self.add_credits_to_user(transaction["user_id"], transaction["amount"])
        
        return PaymeTransactionResponse(
            perform_time=current_time,
            transaction=transaction["id"],
            state=TransactionState.PAID,
            create_time=transaction["create_time"],
            cancel_time=0
        )
    
    def cancel_transaction(self, params: PaymeParams, request_id: int) -> PaymeTransactionResponse:
        """Cancel transaction"""
        logger.info(f"CancelTransaction: {params}")
        
        transaction = self.db.find_one(
            self.collection_name,
            {"id": params.id}
        )
        
        if not transaction:
            raise PaymeError(**self.ERRORS["TransactionNotFound"], request_id=request_id)
        
        current_time = int(time.time() * 1000)
        
        # If transaction is in positive state, cancel it
        if transaction["state"] > 0:
            new_state = -abs(transaction["state"])
            self.db.update_one(
                self.collection_name,
                {"id": params.id},
                {
                    "state": new_state,
                    "reason": params.reason,
                    "cancel_time": current_time
                }
            )
            
            # If transaction was paid, rollback credits
            if transaction["state"] == TransactionState.PAID:
                # await self.rollback_credits(transaction["user_id"], transaction["amount"])
                pass
        
        return PaymeTransactionResponse(
            cancel_time=transaction.get("cancel_time", current_time),
            transaction=transaction["id"],
            state=-abs(transaction["state"]),
            create_time=transaction["create_time"],
            perform_time=transaction.get("perform_time", 0),
            reason=params.reason
        )
    
    def get_statement(self, params: PaymeParams, request_id: int) -> List[Dict]:
        """Get statement of transactions"""
        logger.info(f"GetStatement: from={params.from_time}, to={params.to_time}")
        
        query = {
            "create_time": {
                "$gte": params.from_time,
                "$lte": params.to_time
            }
        }
        
        transactions = self.db.find_many(self.collection_name, query)
        
        result = []
        for transaction in transactions:
            result.append({
                "id": transaction["id"],
                "time": transaction["create_time"],
                "amount": transaction["amount"] * 100,  # Convert to tiyin
                "account": {
                    "user_id": transaction["user_id"],
                    "order_id": transaction.get("order_id")
                },
                "create_time": transaction["create_time"],
                "perform_time": transaction.get("perform_time", 0),
                "cancel_time": transaction.get("cancel_time", 0),
                "transaction": transaction["id"],
                "state": transaction["state"],
                "reason": transaction.get("reason")
            })
        
        return result
    
    def generate_payment_link(self, payment_data: PaymePaymentLink) -> str:
        """
        Generate Payme payment link
        Format: https://checkout.paycom.uz/base64(m=merchant_id;ac.user_id=xxx;a=amount)
        Returns the payment URL as a string
        """
        logger.info(f"Generating payment link for user {payment_data.user_id}")
        
        # Convert amount from sum to tiyin
        amount_tiyin = payment_data.amount * 100
        
        # Build parameters
        params_parts = [
            f"m={settings.PAYME_MERCHANT_ID}",
            f"ac.user_id={payment_data.user_id}"
        ]
        
        if payment_data.order_id:
            params_parts.append(f"ac.order_id={payment_data.order_id}")
        
        params_parts.append(f"a={amount_tiyin}")
        
        if payment_data.return_url:
            params_parts.append(f"c={payment_data.return_url}")
        
        # Join parameters with semicolon
        params_string = ";".join(params_parts)
        
        # Encode to base64
        encoded_params = base64.b64encode(params_string.encode()).decode()
        
        # Build payment URL
        payment_url = f"https://checkout.paycom.uz/{encoded_params}"
        
        logger.info(f"Payment link generated: {payment_url}")
        
        return payment_url
