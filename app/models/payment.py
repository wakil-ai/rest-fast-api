# app/models/payment.py

from typing import Optional
from pydantic import BaseModel, Field
from datetime import datetime, timezone
from enum import IntEnum


class TransactionState(IntEnum):
    """Paycom transaction states"""
    CREATED = 1  # Transaction created, awaiting completion
    COMPLETED = 2  # Transaction completed successfully
    CANCELLED = -1  # Transaction cancelled
    CANCELLED_AFTER_COMPLETE = -2  # Transaction cancelled after completion


class OrderStatus(str):
    """Order status values"""
    PENDING = "pending"  # Order created, awaiting payment
    PAID = "paid"  # Payment completed
    CANCELLED = "cancelled"  # Order cancelled
    REFUNDED = "refunded"  # Order refunded


class Order(BaseModel):
    """Order model for credit purchases"""
    order_id: str = Field(..., description="Unique order ID")
    user_id: str = Field(..., description="User ID who created the order")
    amount: int = Field(..., description="Order amount in tiyin (1 sum = 100 tiyin)")
    credit_amount: int = Field(..., description="Number of credits to be added upon payment")
    status: str = Field(default=OrderStatus.PENDING, description="Order status")
    description: Optional[str] = Field(default=None, description="Order description")
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc), description="When the order was created")
    updated_at: Optional[datetime] = Field(default=None, description="When the order was last updated")
    
    class Config:
        json_schema_extra = {
            "example": {
                "order_id": "ORD-12345",
                "user_id": "user123",
                "amount": 10000,  # 100 sum
                "credit_amount": 100,
                "status": "pending",
                "description": "Purchase 100 credits"
            }
        }


class Transaction(BaseModel):
    """Paycom transaction model"""
    transaction_id: str = Field(..., description="Paycom transaction ID")
    order_id: str = Field(..., description="Associated order ID")
    amount: int = Field(..., description="Transaction amount in tiyin")
    state: int = Field(..., description="Transaction state (1=created, 2=completed, -1=cancelled)")
    create_time: int = Field(..., description="Creation timestamp in milliseconds")
    perform_time: Optional[int] = Field(default=None, description="Completion timestamp in milliseconds")
    cancel_time: Optional[int] = Field(default=None, description="Cancellation timestamp in milliseconds")
    reason: Optional[int] = Field(default=None, description="Cancellation reason code")
    
    class Config:
        json_schema_extra = {
            "example": {
                "transaction_id": "5f9c3d4e1c9d440000a1b2c3",
                "order_id": "ORD-12345",
                "amount": 10000,
                "state": 1,
                "create_time": 1609459200000
            }
        }


class OrderCreate(BaseModel):
    """Request model for creating an order"""
    user_id: str = Field(..., description="User ID")
    amount: int = Field(..., description="Order amount in tiyin", gt=0)
    credit_amount: int = Field(..., description="Number of credits to purchase", gt=0)
    description: Optional[str] = Field(default=None, description="Order description")


class OrderResponse(BaseModel):
    """Response model for order"""
    order_id: str
    user_id: str
    amount: int
    credit_amount: int
    status: str
    description: Optional[str]
    created_at: datetime
