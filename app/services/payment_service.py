# app/services/payment_service.py

from datetime import datetime, timezone
from typing import Optional, Dict, Any
import hashlib
from app.db.mongo_handler import MongoHandler
from app.models.payment import Order, Transaction, OrderStatus, TransactionState
from app.core.logger import logger
from app.core.config import settings


class PaymentService:
    """
    Service to manage orders and Paycom transactions.
    Handles order creation, validation, and transaction lifecycle.
    """
    
    ORDERS_COLLECTION = "orders"
    TRANSACTIONS_COLLECTION = "paycom_transactions"
    
    def __init__(self):
        self.mongo_handler = MongoHandler()
    
    def get_order(self, order_id: str) -> Optional[Dict[str, Any]]:
        """
        Get an order by ID.
        
        Args:
            order_id: The order ID
            
        Returns:
            Dict or None: Order document if found
        """
        try:
            collection = self.mongo_handler.db[self.ORDERS_COLLECTION]
            order = collection.find_one({"order_id": order_id})
            return order
        except Exception as e:
            logger.error(f"[PaymentService] Error getting order {order_id}: {str(e)}")
            return None
    
    def validate_order(self, order_id: str, amount: int) -> tuple[bool, Optional[str]]:
        """
        Validate an order for payment processing.
        
        Args:
            order_id: The order ID to validate
            amount: The payment amount in tiyin
            
        Returns:
            tuple: (is_valid, error_message)
        """
        try:
            order = self.get_order(order_id)
            
            if not order:
                return False, f"Order {order_id} not found"
            
            # Check if order is in pending status
            if order.get("status") != OrderStatus.PENDING:
                return False, f"Order {order_id} is not in pending status (current: {order.get('status')})"
            
            # Check if amount matches
            if order.get("amount") != amount:
                return False, f"Amount mismatch for order {order_id}. Expected: {order.get('amount')}, Got: {amount}"
            
            logger.info(f"[PaymentService] Order {order_id} validated successfully")
            return True, None
            
        except Exception as e:
            logger.error(f"[PaymentService] Error validating order {order_id}: {str(e)}")
            return False, f"Validation error: {str(e)}"
    
    def create_order(self, user_id: str, amount: int, credit_amount: int, 
                    description: Optional[str] = None) -> Optional[str]:
        """
        Create a new order for credit purchase.
        
        Args:
            user_id: User ID
            amount: Order amount in tiyin
            credit_amount: Number of credits to purchase
            description: Optional order description
            
        Returns:
            str or None: Order ID if created successfully
        """
        try:
            collection = self.mongo_handler.db[self.ORDERS_COLLECTION]
            
            # Generate order ID (using timestamp + user_id hash)
            timestamp = int(datetime.now(timezone.utc).timestamp())
            hash_suffix = hashlib.md5(f"{user_id}{timestamp}".encode()).hexdigest()[:8]
            order_id = f"ORD-{timestamp}-{hash_suffix}"
            
            order_doc = {
                "order_id": order_id,
                "user_id": user_id,
                "amount": amount,
                "credit_amount": credit_amount,
                "status": OrderStatus.PENDING,
                "description": description,
                "created_at": datetime.now(timezone.utc),
                "updated_at": None
            }
            
            collection.insert_one(order_doc)
            logger.info(f"[PaymentService] Created order {order_id} for user {user_id}: {amount} tiyin for {credit_amount} credits")
            return order_id
            
        except Exception as e:
            logger.error(f"[PaymentService] Error creating order: {str(e)}")
            return None
    
    def update_order_status(self, order_id: str, status: str) -> bool:
        """
        Update order status.
        
        Args:
            order_id: The order ID
            status: New status
            
        Returns:
            bool: True if updated successfully
        """
        try:
            collection = self.mongo_handler.db[self.ORDERS_COLLECTION]
            
            result = collection.update_one(
                {"order_id": order_id},
                {
                    "$set": {
                        "status": status,
                        "updated_at": datetime.now(timezone.utc)
                    }
                }
            )
            
            if result.modified_count > 0:
                logger.info(f"[PaymentService] Updated order {order_id} status to {status}")
                return True
            else:
                logger.warning(f"[PaymentService] No order found with ID {order_id}")
                return False
                
        except Exception as e:
            logger.error(f"[PaymentService] Error updating order status: {str(e)}")
            return False
    
    def get_transaction(self, transaction_id: str) -> Optional[Dict[str, Any]]:
        """
        Get a transaction by ID.
        
        Args:
            transaction_id: The Paycom transaction ID
            
        Returns:
            Dict or None: Transaction document if found
        """
        try:
            collection = self.mongo_handler.db[self.TRANSACTIONS_COLLECTION]
            transaction = collection.find_one({"transaction_id": transaction_id})
            return transaction
        except Exception as e:
            logger.error(f"[PaymentService] Error getting transaction {transaction_id}: {str(e)}")
            return None
    
    def create_transaction(self, transaction_id: str, order_id: str, 
                          amount: int, time_ms: int) -> bool:
        """
        Create a new Paycom transaction.
        
        Args:
            transaction_id: Paycom transaction ID
            order_id: Associated order ID
            amount: Transaction amount in tiyin
            time_ms: Transaction time in milliseconds
            
        Returns:
            bool: True if created successfully
        """
        try:
            collection = self.mongo_handler.db[self.TRANSACTIONS_COLLECTION]
            
            # Check if transaction already exists
            existing = collection.find_one({"transaction_id": transaction_id})
            if existing:
                logger.warning(f"[PaymentService] Transaction {transaction_id} already exists")
                return False
            
            transaction_doc = {
                "transaction_id": transaction_id,
                "order_id": order_id,
                "amount": amount,
                "state": TransactionState.CREATED,
                "create_time": time_ms,
                "perform_time": None,
                "cancel_time": None,
                "reason": None
            }
            
            collection.insert_one(transaction_doc)
            logger.info(f"[PaymentService] Created transaction {transaction_id} for order {order_id}")
            return True
            
        except Exception as e:
            logger.error(f"[PaymentService] Error creating transaction: {str(e)}")
            return False
    
    def perform_transaction(self, transaction_id: str, perform_time: int) -> bool:
        """
        Mark transaction as completed and update order status.
        
        Args:
            transaction_id: The transaction ID
            perform_time: Completion time in milliseconds
            
        Returns:
            bool: True if performed successfully
        """
        try:
            collection = self.mongo_handler.db[self.TRANSACTIONS_COLLECTION]
            
            # Get transaction to find order_id
            transaction = self.get_transaction(transaction_id)
            if not transaction:
                logger.warning(f"[PaymentService] Transaction {transaction_id} not found")
                return False
            
            # Update transaction state
            result = collection.update_one(
                {"transaction_id": transaction_id},
                {
                    "$set": {
                        "state": TransactionState.COMPLETED,
                        "perform_time": perform_time
                    }
                }
            )
            
            if result.modified_count > 0:
                # Update order status to paid
                order_id = transaction.get("order_id")
                self.update_order_status(order_id, OrderStatus.PAID)
                
                # TODO: Add credits to user account here
                # self.add_credits_to_user(order_id)
                
                logger.info(f"[PaymentService] Performed transaction {transaction_id}")
                return True
            
            return False
            
        except Exception as e:
            logger.error(f"[PaymentService] Error performing transaction: {str(e)}")
            return False
    
    def cancel_transaction(self, transaction_id: str, cancel_time: int, reason: int) -> bool:
        """
        Cancel a transaction and update order status.
        
        Args:
            transaction_id: The transaction ID
            cancel_time: Cancellation time in milliseconds
            reason: Cancellation reason code
            
        Returns:
            bool: True if cancelled successfully
        """
        try:
            collection = self.mongo_handler.db[self.TRANSACTIONS_COLLECTION]
            
            # Get transaction to find order_id and current state
            transaction = self.get_transaction(transaction_id)
            if not transaction:
                logger.warning(f"[PaymentService] Transaction {transaction_id} not found")
                return False
            
            # Determine new state based on current state
            current_state = transaction.get("state")
            new_state = TransactionState.CANCELLED_AFTER_COMPLETE if current_state == TransactionState.COMPLETED else TransactionState.CANCELLED
            
            # Update transaction state
            result = collection.update_one(
                {"transaction_id": transaction_id},
                {
                    "$set": {
                        "state": new_state,
                        "cancel_time": cancel_time,
                        "reason": reason
                    }
                }
            )
            
            if result.modified_count > 0:
                # Update order status
                order_id = transaction.get("order_id")
                if new_state == TransactionState.CANCELLED_AFTER_COMPLETE:
                    self.update_order_status(order_id, OrderStatus.REFUNDED)
                    # TODO: Deduct credits from user account here
                else:
                    self.update_order_status(order_id, OrderStatus.CANCELLED)
                
                logger.info(f"[PaymentService] Cancelled transaction {transaction_id}")
                return True
            
            return False
            
        except Exception as e:
            logger.error(f"[PaymentService] Error cancelling transaction: {str(e)}")
            return False
    
    def get_transactions_by_time_range(self, from_time: int, to_time: int) -> list:
        """
        Get transactions within a time range.
        
        Args:
            from_time: Start time in milliseconds
            to_time: End time in milliseconds
            
        Returns:
            list: List of transactions
        """
        try:
            collection = self.mongo_handler.db[self.TRANSACTIONS_COLLECTION]
            
            transactions = list(collection.find({
                "create_time": {
                    "$gte": from_time,
                    "$lte": to_time
                }
            }))
            
            logger.info(f"[PaymentService] Retrieved {len(transactions)} transactions")
            return transactions
            
        except Exception as e:
            logger.error(f"[PaymentService] Error getting transactions: {str(e)}")
            return []
