# app/models/order.py

from typing import Optional
from pydantic import BaseModel, Field
from datetime import datetime


class Order(BaseModel):
    """Order model for payment processing"""
    order_id: str = Field(..., description="Unique order identifier")
    user_id: str = Field(..., description="User ID who created the order")
    amount: int = Field(..., description="Order amount in tiyin (coins)")
    description: Optional[str] = Field(default=None, description="Order description")
    status: str = Field(default="pending", description="Order status: pending, paid, cancelled")
    created_at: datetime = Field(default_factory=datetime.utcnow, description="Order creation timestamp")
    paid_at: Optional[datetime] = Field(default=None, description="Payment completion timestamp")


class OrderCreate(BaseModel):
    """Request model for creating an order"""
    user_id: str = Field(..., description="User ID who is creating the order")
    amount: int = Field(..., description="Order amount in tiyin (coins)", gt=0)
    description: Optional[str] = Field(default=None, description="Order description")


class OrderResponse(BaseModel):
    """Response model for order"""
    order_id: str
    user_id: str
    amount: int
    description: Optional[str] = None
    status: str
    created_at: datetime
    paid_at: Optional[datetime] = None
