from typing import Literal

from pydantic import BaseModel


class ClickInitRequest(BaseModel):
    amount: int | None = None
    user_id: str
    callback_url: str
    order_id: str | None = None
    subscription_tier: Literal["standard", "pro", "test"] | None = None
    subscription_period: Literal["monthly", "yearly"] | None = None


class ClickInitResponse(BaseModel):
    order_id: str
    link: str


class ClickPrepareResponse(BaseModel):
    click_trans_id: int
    merchant_trans_id: str
    merchant_prepare_id: int | None = None
    error: int
    error_note: str


class ClickCompleteResponse(BaseModel):
    click_trans_id: int
    merchant_trans_id: str
    merchant_confirm_id: int | None = None
    error: int
    error_note: str
