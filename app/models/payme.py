"""Payme payment integration models"""

from pydantic import BaseModel, Field
from typing import Optional, Dict, Any, Literal
from datetime import datetime
from enum import IntEnum


class TransactionState(IntEnum):
    """Transaction states according to Payme documentation"""
    PENDING = 1  # Transaction created
    PAID = 2  # Transaction completed
    PENDING_CANCELED = -1  # Transaction canceled (pending state)
    PAID_CANCELED = -2  # Transaction canceled (paid state)


class PaymeMethod(str):
    """Payme merchant API methods"""
    CHECK_PERFORM_TRANSACTION = "CheckPerformTransaction"
    CHECK_TRANSACTION = "CheckTransaction"
    CREATE_TRANSACTION = "CreateTransaction"
    PERFORM_TRANSACTION = "PerformTransaction"
    CANCEL_TRANSACTION = "CancelTransaction"
    GET_STATEMENT = "GetStatement"


class PaymeAccount(BaseModel):
    """Account parameters for Payme transactions"""
    user_id: str = Field(..., description="User ID from your system")
    order_id: Optional[str] = Field(None, description="Order ID (optional)")
    
    class Config:
        extra = "allow"  # Allow additional fields


class PaymeParams(BaseModel):
    """Parameters for Payme merchant API requests"""
    amount: Optional[int] = Field(None, description="Amount in tiyin (1 sum = 100 tiyin)")
    account: Optional[PaymeAccount] = None
    id: Optional[str] = Field(None, description="Transaction ID")
    time: Optional[int] = Field(None, description="Unix timestamp in milliseconds")
    reason: Optional[int] = Field(None, description="Cancellation reason")
    from_time: Optional[int] = Field(None, alias="from", description="Start timestamp for GetStatement")
    to_time: Optional[int] = Field(None, alias="to", description="End timestamp for GetStatement")


class PaymeRequest(BaseModel):
    """Payme merchant API request"""
    method: str = Field(..., description="Payme method name")
    params: PaymeParams
    id: int = Field(..., description="Request ID")


class PaymeError(BaseModel):
    """Payme error response"""
    code: int
    message: Dict[str, str]
    data: Optional[str] = None


class PaymeTransactionResponse(BaseModel):
    """Response for transaction operations"""
    create_time: int
    perform_time: int = 0
    cancel_time: int = 0
    transaction: str
    state: int
    reason: Optional[int] = None


class PaymeCheckPerformTransactionResponse(BaseModel):
    """Response for CheckPerformTransaction"""
    allow: bool


class PaymeResponse(BaseModel):
    """Payme merchant API response"""
    result: Optional[Any] = None
    error: Optional[PaymeError] = None
    id: int


class PaymeTransaction(BaseModel):
    """Payme transaction model for MongoDB"""
    id: str = Field(..., description="Payme transaction ID")
    user_id: str = Field(..., description="User ID")
    order_id: Optional[str] = Field(None, description="Order ID")
    state: TransactionState = Field(default=TransactionState.PENDING)
    amount: int = Field(..., description="Amount in sum (not tiyin)")
    create_time: int = Field(..., description="Creation timestamp")
    perform_time: int = Field(default=0)
    cancel_time: int = Field(default=0)
    reason: Optional[int] = Field(None, description="Cancellation reason")
    provider: str = Field(default="payme")
    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)


class PaymePaymentLink(BaseModel):
    """Model for generating Payme payment link"""
    user_id: str = Field(..., description="User ID")
    amount: int = Field(..., description="Amount in sum (not tiyin)")
    order_id: Optional[str] = Field(None, description="Optional order ID")
    return_url: Optional[str] = Field(None, description="URL to return after payment")
    
    
class PaymePaymentLinkResponse(BaseModel):
    """Response for payment link generation"""
    payment_url: str
    order_id: Optional[str] = None