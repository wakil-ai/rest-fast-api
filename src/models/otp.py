import re
from typing import Literal

from pydantic import BaseModel, Field, field_validator

# E.164: leading +, 8-15 digits, first digit 1-9.
_E164_PATTERN = re.compile(r"^\+[1-9]\d{7,14}$")


def _normalize_phone(value: str) -> str:
    # Strip common separators users tend to paste; keep leading +.
    cleaned = re.sub(r"[\s\-()]+", "", value or "")
    return cleaned


class SendOTPRequest(BaseModel):
    phone_number: str = Field(
        ...,
        description="Recipient phone in E.164 format (e.g. +998901234567).",
        examples=["+998901234567"],
    )
    channel: Literal["sms", "call", "whatsapp"] = Field(
        default="sms",
        description="Delivery channel for the verification code.",
    )
    locale: Literal["uz", "ru", "en"] = "uz"

    @field_validator("phone_number")
    @classmethod
    def _validate_phone(cls, value: str) -> str:
        cleaned = _normalize_phone(value)
        if not _E164_PATTERN.match(cleaned):
            raise ValueError(
                "phone_number must be E.164 format (e.g. +998901234567)"
            )
        return cleaned


class SendOTPResponse(BaseModel):
    success: bool
    request_id: str
    status: Literal["pending"]
    channel: Literal["sms", "call", "whatsapp"]
    expires_in: int
    resend_after: int


class VerifyOTPRequest(BaseModel):
    phone_number: str = Field(
        ...,
        description="Same phone number used for the send step (E.164).",
        examples=["+998901234567"],
    )
    code: str = Field(
        ...,
        min_length=4,
        max_length=10,
        description="OTP code the user received.",
        examples=["123456"],
    )

    @field_validator("phone_number")
    @classmethod
    def _validate_phone(cls, value: str) -> str:
        cleaned = _normalize_phone(value)
        if not _E164_PATTERN.match(cleaned):
            raise ValueError(
                "phone_number must be E.164 format (e.g. +998901234567)"
            )
        return cleaned

    @field_validator("code")
    @classmethod
    def _validate_code(cls, value: str) -> str:
        value = value.strip()
        if not value.isdigit():
            raise ValueError("code must contain only digits")
        return value


class VerifyOTPResponse(BaseModel):
    success: bool = Field(..., description="True when the code is approved.")
    status: Literal["approved"]
    verification_token: str
    phone_number: str = Field(..., description="Phone number that was verified.")
