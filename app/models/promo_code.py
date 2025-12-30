# app/models/promo_code.py

from typing import Optional
from pydantic import BaseModel, Field
from datetime import datetime


class PromoCode(BaseModel):
    """Promo code model for unlimited requests"""
    code: str = Field(..., description="Unique promo code string")
    is_active: bool = Field(default=True, description="Whether the promo code is active")
    created_at: Optional[datetime] = Field(default=None, description="When the promo code was created")
    created_by: Optional[str] = Field(default=None, description="Admin who created the code")
    description: Optional[str] = Field(default=None, description="Description or notes about the promo code")


class PromoCodeCreate(BaseModel):
    """Request model for creating a promo code"""
    code: str = Field(..., description="Unique promo code string", min_length=3, max_length=50)
    description: Optional[str] = Field(default=None, description="Description or notes about the promo code")


class PromoCodeResponse(BaseModel):
    """Response model for promo code"""
    code: str
    is_active: bool
    created_at: datetime
    created_by: Optional[str] = None
    description: Optional[str] = None


class UserPromoCode(BaseModel):
    """Model for assigning promo code to user"""
    user_id: str = Field(..., description="User ID to assign promo code to")
    promo_code: str = Field(..., description="Promo code to assign")


class UserPromoCodeResponse(BaseModel):
    """Response model for user promo code assignment"""
    user_id: str
    promo_code: str
    assigned_at: datetime
    has_unlimited_access: bool = True
