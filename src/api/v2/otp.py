from fastapi import APIRouter, Depends

from core.dependencies import get_otp_service
from models.otp import SendOTPRequest, SendOTPResponse, VerifyOTPRequest, VerifyOTPResponse
from security.dependencies import get_current_user_id
from services import OTPService

router = APIRouter(prefix='/auth/otp', tags=['Auth for Login'])


@router.post('/send', response_model=SendOTPResponse)
async def send_otp_endpoint(
    request: SendOTPRequest,
    user_id: str = Depends(get_current_user_id),
    otp_service: OTPService = Depends(get_otp_service),
) -> SendOTPResponse:
    """Send a JWT-bound challenge; local SMS uses Eskiz, other channels Twilio."""
    result = await otp_service.send_otp(phone_number=request.phone_number, channel=request.channel, locale=request.locale, user_id=user_id)
    return SendOTPResponse(success=True, **result)


@router.post('/verify', response_model=VerifyOTPResponse)
async def verify_otp_endpoint(
    request: VerifyOTPRequest,
    user_id: str = Depends(get_current_user_id),
    otp_service: OTPService = Depends(get_otp_service),
) -> VerifyOTPResponse:
    """Approve a code and mint a short-lived, one-use phone persistence proof."""
    result = await otp_service.verify_otp(phone_number=request.phone_number, code=request.code, user_id=user_id)
    return VerifyOTPResponse(success=True, phone_number=request.phone_number, **result)
