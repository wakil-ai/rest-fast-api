from datetime import datetime

from pydantic import BaseModel, Field


class PromoCode(BaseModel):
    """Promo code model with deadline and credit amount"""

    code: str = Field(..., description="Unique promo code string")
    is_active: bool = Field(
        default=True, description="Whether the promo code is active"
    )
    expiration_date: datetime | None = Field(
        default=None, description="Expiration date (None = forever)"
    )
    credit_amount: int | None = Field(
        default=None, description="Daily credit amount (None = unlimited)"
    )
    created_at: datetime | None = Field(
        default=None, description="When the promo code was created"
    )
    created_by: str | None = Field(
        default=None, description="Admin who created the code"
    )
    description: str | None = Field(
        default=None, description="Description or notes about the promo code"
    )


class PromoCodeCreate(BaseModel):
    """Request model for creating a promo code"""

    code: str = Field(
        ..., description="Unique promo code string", min_length=3, max_length=50
    )
    expiration_date: datetime | None = Field(
        default=None, description="Expiration date (omit for forever)"
    )
    credit_amount: int | None = Field(
        default=None, description="Daily credit amount (omit for unlimited)", ge=1
    )
    description: str | None = Field(
        default=None, description="Description or notes about the promo code"
    )
    super_secret_admin_key: str = Field(
        ..., description="Admin key required to create promo codes"
    )


class PromoCodeResponse(BaseModel):
    """Response model for promo code"""

    code: str
    is_active: bool
    expiration_date: datetime | None = None
    credit_amount: int | None = None
    created_at: datetime
    created_by: str | None = None
    description: str | None = None


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
