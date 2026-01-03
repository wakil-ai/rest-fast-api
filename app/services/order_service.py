# app/services/order_service.py

from typing import Optional, Dict, Any
from datetime import datetime
from app.db.mongo_handler import MongoHandler
from app.core.logger import logger
from app.models.order import Order, OrderCreate
import secrets


class OrderService:
    """Service for managing orders in MongoDB"""
    
    ORDERS_COLLECTION = "orders"
    
    def __init__(self):
        """Initialize the order service with MongoDB handler"""
        try:
            self.mongo_handler = MongoHandler()
        except Exception as e:
            logger.error(f"[OrderService] Failed to initialize MongoDB handler: {str(e)}")
            self.mongo_handler = None
    
    def _generate_order_id(self) -> str:
        """
        Generate a unique order ID
        
        Returns:
            str: 12-character alphanumeric order ID
        """
        return secrets.token_hex(6)
    
    def create_order(self, order_data: OrderCreate) -> Optional[Order]:
        """
        Create a new order
        
        Args:
            order_data: Order creation data
            
        Returns:
            Order object if successful, None otherwise
        """
        if not self.mongo_handler:
            logger.error("[OrderService] MongoDB handler not initialized")
            return None
        
        try:
            order_id = self._generate_order_id()
            order = Order(
                order_id=order_id,
                user_id=order_data.user_id,
                amount=order_data.amount,
                description=order_data.description,
                status="pending",
                created_at=datetime.utcnow()
            )
            
            # Insert into MongoDB
            order_dict = order.model_dump()
            self.mongo_handler.db[self.ORDERS_COLLECTION].insert_one(order_dict)
            
            logger.info(f"[OrderService] Created order: {order_id} for user {order_data.user_id}")
            return order
            
        except Exception as e:
            logger.error(f"[OrderService] Error creating order: {str(e)}")
            return None
    
    def get_order(self, order_id: str) -> Optional[Order]:
        """
        Get order by ID
        
        Args:
            order_id: Order identifier
            
        Returns:
            Order object if found, None otherwise
        """
        if not self.mongo_handler:
            logger.error("[OrderService] MongoDB handler not initialized")
            return None
        
        try:
            order_dict = self.mongo_handler.db[self.ORDERS_COLLECTION].find_one(
                {"order_id": order_id}
            )
            
            if order_dict:
                # Remove MongoDB's _id field
                order_dict.pop('_id', None)
                return Order(**order_dict)
            
            return None
            
        except Exception as e:
            logger.error(f"[OrderService] Error getting order {order_id}: {str(e)}")
            return None
    
    def update_order_status(
        self, 
        order_id: str, 
        status: str, 
        paid_at: Optional[datetime] = None
    ) -> bool:
        """
        Update order status
        
        Args:
            order_id: Order identifier
            status: New status (pending, paid, cancelled)
            paid_at: Payment timestamp (optional)
            
        Returns:
            True if successful, False otherwise
        """
        if not self.mongo_handler:
            logger.error("[OrderService] MongoDB handler not initialized")
            return False
        
        try:
            update_data: Dict[str, Any] = {"status": status}
            if paid_at:
                update_data["paid_at"] = paid_at
            
            result = self.mongo_handler.db[self.ORDERS_COLLECTION].update_one(
                {"order_id": order_id},
                {"$set": update_data}
            )
            
            if result.modified_count > 0:
                logger.info(f"[OrderService] Updated order {order_id} status to {status}")
                return True
            
            logger.warning(f"[OrderService] Order {order_id} not found or status unchanged")
            return False
            
        except Exception as e:
            logger.error(f"[OrderService] Error updating order {order_id}: {str(e)}")
            return False
    
    def validate_order(self, order_id: str, amount: int) -> tuple[bool, Optional[str]]:
        """
        Validate that order exists and amount matches
        
        Args:
            order_id: Order identifier
            amount: Amount to validate in tiyin (coins)
            
        Returns:
            Tuple of (is_valid, error_message)
        """
        if not self.mongo_handler:
            return False, "Database connection not available"
        
        order = self.get_order(order_id)
        
        if not order:
            return False, f"Order {order_id} not found"
        
        if order.status == "paid":
            return False, f"Order {order_id} is already paid"
        
        if order.status == "cancelled":
            return False, f"Order {order_id} is cancelled"
        
        if order.amount != amount:
            return False, f"Amount mismatch: expected {order.amount}, got {amount}"
        
        return True, None
