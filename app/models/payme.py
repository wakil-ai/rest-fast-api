from enum import IntEnum
from pydantic import BaseModel


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


from datetime import datetime
from bson import ObjectId


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
        create_time: int = None,
        perform_time: int = 0,
        cancel_time: int = 0,
        reason: int = None,
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
    amount: int  # Amount in smallest currency unit (e.g. cents)
    user_id: str  # ID of the user for whom the link is created
    callback_url: str  # URL to redirect after payment


class PaymentLinkResponse(BaseModel):
    link: str  # Generated payment link URL


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
