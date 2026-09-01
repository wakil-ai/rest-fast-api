from datetime import datetime
from enum import IntEnum
from typing import Literal

from bson import ObjectId
from pydantic import BaseModel, Field

from core.exceptions import ChatException


SubscriptionTier = Literal["basic", "standard", "premium", "pro", "test"]
# Keep in sync with core.subscription_tiers.SUBSCRIPTION_PERIODS — a Literal can't
# be built from that tuple, so a test asserts the two agree.
SubscriptionPeriod = Literal[
    "daily", "monthly", "quarterly", "semiannual", "yearly"
]


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
    subscription_tier: SubscriptionTier | None = Field(default=None)
    subscription_period: SubscriptionPeriod | None = Field(default=None)


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
    # Promo campaign fields — all None when no discount applies to this plan.
    # See core.subscription_promo for how amount_sum gets discounted.
    list_price_sum: int | None = None
    discount_percent: int | None = None
    promo_ends_at_ms: int | None = None


class SubscriptionCatalogResponse(BaseModel):
    plans: list[SubscriptionPlan]


class UserSubscriptionResponse(BaseModel):
    user_id: str
    active: bool
    tier: str | None = None
    period: str | None = None
    daily_credits: int | None = None  # Legacy field; 0 for pool-based tiers.
    total_credits: int | None = None  # Purchased pool for paid tiers.
    credits_remaining: int | None = None  # Remaining pool for paid tiers.
    start_ms: int | None = None
    end_ms: int | None = None

    # Optional daily pass (pay-per-day) info; does not override subscription.
    daily_pass_active: bool | None = None
    daily_pass_tier: str | None = None
    daily_pass_daily_credits: int | None = None
    daily_pass_start_ms: int | None = None
    daily_pass_end_ms: int | None = None
    combined_daily_credits: int | None = None
    effective_daily_credit_limit: int | None = None
    today_credits_used: int | None = None
    today_remaining_credits: int | None = None
    uses_combined_credit_pool: bool = True
    can_upload: bool = False
    # Which rail granted the active subscription: "appstore" | "payme" | "click" | "uzum".
    source: str | None = None


# Apple App Store (StoreKit 2) Models
class AppStoreError(ChatException):
    """Raised inside AppStoreService; rendered by FastAPI as its status code."""

    def __init__(self, detail: str, *, status_code: int = 400):
        super().__init__(detail=detail, status_code=status_code)


class AppStoreVerifyRequest(BaseModel):
    user_id: str
    # StoreKit Transaction.jwsRepresentation — signed by Apple, verified server-side.
    jws: str
    # UUIDv5 derived from user_id on the client. Not used server-side yet (ownership
    # is pinned via the signed payload's appAccountToken); accepted for forward compat.
    app_account_token: str | None = None
    # "Production" | "Sandbox" | "Xcode"
    environment: str
    bundle_id: str


class AppStoreVerifyResponse(BaseModel):
    status: str  # "active" | "inactive" | "revoked"
    tier: str | None = None
    period: str | None = None
    original_transaction_id: str | None = None
    expires_ms: int | None = None
    daily_credits: int | None = None


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
    subscription_tier: SubscriptionTier | None = None
    subscription_period: SubscriptionPeriod | None = None


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


# Uzum Merchant API Models
class UzumResponseStatus:
    OK = "OK"
    FAILED = "FAILED"
    CREATED = "CREATED"
    CONFIRMED = "CONFIRMED"
    REVERSED = "REVERSED"


class UzumTransactionState:
    """Internal representation of Uzum transaction states."""

    CREATED = "CREATED"
    CONFIRMED = "CONFIRMED"
    REVERSED = "REVERSED"


class UzumError:
    """Error codes per Uzum Merchant API spec (string, not int)."""

    ACCESS_DENIED = "10001"
    JSON_PARSING_ERROR = "10002"
    INVALID_OPERATION = "10003"
    MISSING_REQUIRED_PARAMETERS = "10005"
    INVALID_SERVICE_ID = "10006"
    ADDITIONAL_PAYMENT_ATTRIBUTE_NOT_FOUND = "10007"
    PAYMENT_ALREADY_MADE = "10008"
    PAYMENT_CANCELLED = "10009"
    DATA_VERIFICATION_ERROR = "99999"


class UzumServiceError(Exception):
    """Raised inside UzumService to signal a 400 response with a specific error code."""

    def __init__(self, error_code: str, *, http_status: int = 400):
        self.error_code = error_code
        self.http_status = http_status
        super().__init__(error_code)


class UzumCheckRequest(BaseModel):
    serviceId: int
    timestamp: int
    params: dict


class UzumCreateRequest(BaseModel):
    serviceId: int
    timestamp: int
    transId: str
    params: dict
    amount: int  # in tiyin


class UzumConfirmRequest(BaseModel):
    serviceId: int
    timestamp: int
    transId: str
    paymentSource: str | None = None
    tariff: str | None = None
    processingReferenceNumber: str | None = None
    phone: str | None = None
    cardType: int | None = None


class UzumReverseRequest(BaseModel):
    serviceId: int
    timestamp: int
    transId: str


class UzumStatusRequest(BaseModel):
    serviceId: int
    timestamp: int
    transId: str


# DT Team Subscription Models
class DTSubscriptionApplyResponse(BaseModel):
    """Response for DT team subscription application"""

    success: bool = True
    user_id: str
    tier: str
    period: str
    daily_credits: int  # 0 for pool-based tiers (standard/pro).
    start_ms: int
    end_ms: int
    total_credits: int
    credits_remaining: int


class DTInitRequest(BaseModel):
    amount: int | None = None
    user_id: str
    order_id: str | None = None
    subscription_tier: SubscriptionTier | None = None
    subscription_period: SubscriptionPeriod | None = None
