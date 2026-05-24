from fastapi import APIRouter, Depends

from app.core.dependencies import get_otp_service
from app.models.otp import (
    SendOTPRequest,
    SendOTPResponse,
    VerifyOTPRequest,
    VerifyOTPResponse,
)
from app.services import OTPService

router = APIRouter(prefix="/auth/otp", tags=["Auth for Login"])


@router.post("/send", response_model=SendOTPResponse)
async def send_otp_endpoint(
    request: SendOTPRequest,
    otp_service: OTPService = Depends(get_otp_service),
) -> SendOTPResponse:
    """Send a verification code to the given phone number via Twilio Verify.

    Rate-limited per phone number (see OTP_SEND_LIMIT_PER_PHONE).
    """
    result = await otp_service.send_otp(
        phone_number=request.phone_number,
        channel=request.channel,
    )
    return SendOTPResponse(
        success=True,
        sid=result["sid"],
        status=result["status"],
        channel=result["channel"],
    )


@router.post("/verify", response_model=VerifyOTPResponse)
async def verify_otp_endpoint(
    request: VerifyOTPRequest,
    otp_service: OTPService = Depends(get_otp_service),
) -> VerifyOTPResponse:
    """Verify an OTP code previously sent to the phone number.

    Returns 400 if the code is invalid or expired, 429 if the phone
    has exceeded the per-window verify cap.
    """
    result = await otp_service.verify_otp(
        phone_number=request.phone_number,
        code=request.code,
    )
    return VerifyOTPResponse(
        success=True,
        status=result["status"],
        phone_number=request.phone_number,
    )
