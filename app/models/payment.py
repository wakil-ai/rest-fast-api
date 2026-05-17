from datetime import datetime
from enum import IntEnum
from typing import Literal

from bson import ObjectId
from pydantic import BaseModel, Field


# Methods
class PaymeMethod:
    CheckPerformTransaction = "CheckPerformTransaction"
    CheckTransaction = "CheckTransaction"
    CreateTransaction = "CreateTransaction"
    PerformTransaction = "PerformTransaction"
    CancelTransaction = "CancelTransaction"
    GetStatement = "GetStatement"
    SetFiscalData = "SetFiscalData"


# Errors
class TransactionError(Exception):
    def __init__(self, error, request_id, data=None):
        self.name = error["name"]
        self.code = error["code"]
        self.message = error["message"]
        self.data = data
        self.request_id = request_id

        super().__init__(self.message.get("en"))

    def to_payme_response(self):
        payload = {
            "code": self.code,
            "message": self.message,
        }
        if self.data:
            payload["data"] = self.data
        return payload


class SubscriptionEligibilityError(ValueError):
    def __init__(
        self,
        *,
        code: str,
        message: str,
        active_daily_pass_end_ms: int | None = None,
        active_subscription_end_ms: int | None = None,
        active_subscription_tier: str | None = None,
        active_subscription_period: str | None = None,
    ):
        self.code = code
        self.message = message
        self.active_daily_pass_end_ms = active_daily_pass_end_ms
        self.active_subscription_end_ms = active_subscription_end_ms
        self.active_subscription_tier = active_subscription_tier
        self.active_subscription_period = active_subscription_period
        super().__init__(message)

    def to_detail(self) -> dict:
        detail = {
            "code": self.code,
            "message": self.message,
        }
        if self.active_daily_pass_end_ms is not None:
            detail["active_daily_pass_end_ms"] = self.active_daily_pass_end_ms
        if self.active_subscription_end_ms is not None:
            detail["active_subscription_end_ms"] = self.active_subscription_end_ms
        if self.active_subscription_tier is not None:
            detail["active_subscription_tier"] = self.active_subscription_tier
        if self.active_subscription_period is not None:
            detail["active_subscription_period"] = self.active_subscription_period
        return detail


class TransactionModel:
    """
    This is a schema reference for documentation & consistency.
    MongoDB does not enforce schema at runtime.
    """

    def __init__(
        self,
        id: str,
        user: ObjectId,
        state: int,
        amount: int,
        provider: str,
        create_time: int | None = None,
        perform_time: int = 0,
        cancel_time: int = 0,
        reason: int | None = None,
    ):
        self.id = id
        self.user = user
        self.state = state
        self.amount = amount
        self.provider = provider
        self.create_time = create_time or int(datetime.utcnow().timestamp() * 1000)
        self.perform_time = perform_time
        self.cancel_time = cancel_time
        self.reason = reason

    def to_dict(self):
        return {
            "id": self.id,
            "user": self.user,
            "state": self.state,
            "amount": self.amount,
            "create_time": self.create_time,
            "perform_time": self.perform_time,
            "cancel_time": self.cancel_time,
            "reason": self.reason,
            "provider": self.provider,
            "createdAt": datetime.utcnow(),
            "updatedAt": datetime.utcnow(),
        }


class PaymeError:
    InvalidAmount = {
        "name": "InvalidAmount",
        "code": -31001,
        "message": {
            "uz": "Noto'g'ri summa",
            "ru": "Недопустимая сумма",
            "en": "Invalid amount",
        },
    }

    UserNotFound = {
        "name": "UserNotFound",
        "code": -31050,
        "message": {
            "uz": "Biz sizning hisobingizni topolmadik.",
            "ru": "Мы не нашли вашу учетную запись",
            "en": "We couldn't find your account",
        },
    }

    CantDoOperation = {
        "name": "CantDoOperation",
        "code": -31008,
        "message": {
            "uz": "Biz operatsiyani bajara olmaymiz",
            "ru": "Мы не можем сделать операцию",
            "en": "We can't do operation",
        },
    }

    TransactionNotFound = {
        "name": "TransactionNotFound",
        "code": -31003,
        "message": {
            "uz": "Tranzaktsiya topilmadi",
            "ru": "Транзакция не найдена",
            "en": "Transaction not found",
        },
    }

    AlreadyDone = {
        "name": "AlreadyDone",
        "code": -31060,
        "message": {
            "uz": "Mahsulot uchun to'lov qilingan",
            "ru": "Оплачено за товар",
            "en": "Paid for the product",
        },
    }

    Pending = {
        "name": "Pending",
        "code": -31050,
        "message": {
            "uz": "Mahsulot uchun to'lov kutilayapti",
            "ru": "Ожидается оплата товар",
            "en": "Payment for the product is pending",
        },
    }

    InvalidAuthorization = {
        "name": "InvalidAuthorization",
        "code": -32504,
        "message": {
            "uz": "Avtorizatsiya yaroqsiz",
            "ru": "Авторизация недействительна",
            "en": "Authorization invalid",
        },
    }

    FiscalReceiptNotFound = {
        "name": "FiscalReceiptNotFound",
        "code": -32001,
        "message": {
            "uz": "Chek bunday id bilan topilmadi",
            "ru": "Чек с таким id не найден",
            "en": "Receipt with this id not found",
        },
    }

    InvalidJSON = {
        "name": "InvalidJSON",
        "code": -32700,
        "message": {
            "uz": "Yaroqsiz JSON ob'ekti yuborilgan",
            "ru": "Отправлен не валидный JSON объект",
            "en": "Invalid JSON object sent",
        },
    }

    InvalidParams = {
        "name": "InvalidParams",
        "code": -32602,
        "message": {
            "uz": "Yaroqsiz parametrlar",
            "ru": "Не валидные параметры",
            "en": "Invalid parameters",
        },
    }


# Payme Data Fields
class PaymeData:
    UserId = "user_id"


class TransactionState(IntEnum):
    Pending = 1
    Paid = 2
    PendingCanceled = -1
    PaidCanceled = -2


class PaymentLinkRequest(BaseModel):
    # For this API, `amount` is expected to be in SUM (UZS).
    # Payme Merchant API itself uses TIYIN, but checkout link generation in our backend accepts SUM.
    amount: int
    user_id: str  # ID of the user for whom the link is created
    order_id: str | None = None  # Optional order/invoice id to include in `ac.order_id`
    callback_url: str  # URL to redirect after payment


class PaymentLinkResponse(BaseModel):
    link: str  # Generated payment link URL


class PaymeInitRequest(BaseModel):
    # If `subscription_tier` + `subscription_period` are provided, amount can be omitted
    # and will be derived server-side from the subscription catalog.
    amount: int | None = None  # Amount in SUM (UZS)
    user_id: str
    callback_url: str
    order_id: str | None = None  # If not provided, server generates a new one
    subscription_tier: Literal["daily", "standard", "pro", "test"] | None = Field(
        default=None
    )
    subscription_period: Literal["daily", "monthly", "yearly"] | None = Field(
        default=None
    )
    order_id: str | None = None  # Optional order/invoice id to include in `ac.order_id`


class PaymeInitResponse(BaseModel):
    order_id: str
    link: str


class SubscriptionPlan(BaseModel):
    tier: str
    period: str
    amount_sum: int
    daily_credits: int
    total_credits: int
    days: int


class SubscriptionCatalogResponse(BaseModel):
    plans: list[SubscriptionPlan]


class UserSubscriptionResponse(BaseModel):
    user_id: str
    active: bool
    tier: str | None = None
    period: str | None = None
    daily_credits: int | None = None
    start_ms: int | None = None
    end_ms: int | None = None

    # Optional daily pass (pay-per-day) info; does not override subscription.
    daily_pass_active: bool | None = None
    daily_pass_daily_credits: int | None = None
    daily_pass_start_ms: int | None = None
    daily_pass_end_ms: int | None = None
    combined_daily_credits: int | None = None
    effective_daily_credit_limit: int | None = None
    today_credits_used: int | None = None
    today_remaining_credits: int | None = None
    uses_combined_credit_pool: bool = True


class FiscalData(BaseModel):
    receipt_id: int | str
    status_code: int
    message: str
    terminal_id: str
    fiscal_sign: str
    qr_code_url: str
    date: str


class SetFiscalDataRequest(BaseModel):
    id: str  # Transaction ID
    type: str  # "PERFORM" or "CANCEL"
    fiscal_data: FiscalData


# Click Models
class ClickInitRequest(BaseModel):
    amount: int | None = None
    user_id: str
    callback_url: str
    order_id: str | None = None
    subscription_tier: Literal["daily", "standard", "pro", "test"] | None = None
    subscription_period: Literal["daily", "monthly", "yearly"] | None = None


class ClickInitResponse(BaseModel):
    order_id: str
    link: str


class ClickPrepareResponse(BaseModel):
    click_trans_id: int
    merchant_trans_id: str
    merchant_prepare_id: int | None = None
    error: int
    error_note: str


class ClickError:
    SUCCESS = 0
    SIGN_CHECK_FAILED = -1
    INCORRECT_AMOUNT = -2
    ACTION_NOT_FOUND = -3
    ALREADY_PAID = -4
    USER_DOES_NOT_EXIST = -5
    TRANSACTION_DOES_NOT_EXIST = -6
    FAILED_TO_UPDATE_USER = -7
    ERROR_IN_REQUEST = -8
    TRANSACTION_CANCELLED = -9


class ClickCompleteResponse(BaseModel):
    click_trans_id: int
    merchant_trans_id: str
    merchant_confirm_id: int | None = None
    error: int
    error_note: str


# DT Team Subscription Models
class DTSubscriptionApplyResponse(BaseModel):
    """Response for DT team subscription application"""

    success: bool = True
    user_id: str
    tier: str
    period: str
    daily_credits: int
    start_ms: int
    end_ms: int
    total_credits: int


class DTInitRequest(BaseModel):
    amount: int | None = None
    user_id: str
    order_id: str | None = None
    subscription_tier: Literal["daily", "standard", "pro", "test"] | None = None
    subscription_period: Literal["daily", "monthly", "yearly"] | None = None
