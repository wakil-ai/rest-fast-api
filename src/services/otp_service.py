import asyncio
from typing import Literal

from twilio.base.exceptions import TwilioRestException
from twilio.rest import Client

from core.config import settings
from core.exceptions import (
    OTPRateLimitExceededException,
    OTPSendFailedException,
    OTPServiceNotConfiguredException,
    OTPVerificationFailedException,
)
from core.logger import logger
from services.redis_service import RedisService


def _mask_phone(phone: str) -> str:
    if len(phone) <= 4:
        return "***"
    return f"{phone[:3]}***{phone[-2:]}"


class OTPService:
    """Twilio Verify v2 wrapper with per-phone Redis rate limiting.

    Twilio's Verify API already enforces per-code attempt caps (5 checks
    per code, ~10 minute code lifetime). The Redis counters here add a
    coarser per-phone burst limit so a single number cannot drain quota
    or rack up SMS cost.
    """

    SEND_RL_PREFIX = "otp:rl:send:"
    VERIFY_RL_PREFIX = "otp:rl:verify:"

    def __init__(self) -> None:
        self._client: Client | None = None
        self._service_sid: str | None = settings.TWILIO_VERIFY_SERVICE_SID
        if (
            settings.TWILIO_ACCOUNT_SID
            and settings.TWILIO_AUTH_TOKEN
            and self._service_sid
        ):
            self._client = Client(
                settings.TWILIO_ACCOUNT_SID,
                settings.TWILIO_AUTH_TOKEN,
            )
            logger.info("[OTPService] Initialized Twilio Verify client")
        else:
            logger.warning(
                "[OTPService] Twilio credentials missing; OTP endpoints will return 503"
            )
        self._redis = RedisService()

    def _ensure_configured(self) -> None:
        if self._client is None or not self._service_sid:
            raise OTPServiceNotConfiguredException()

    def _enforce_rate_limit(
        self,
        prefix: str,
        phone: str,
        limit: int,
        window_seconds: int,
    ) -> None:
        key = f"{prefix}{phone}"
        try:
            pipe = self._redis.redis.pipeline()
            pipe.incr(key)
            pipe.expire(key, window_seconds, nx=True)
            count, _ = pipe.execute()
        except Exception as e:
            # Fail open on Redis outages — better to send the OTP than to lock users out.
            logger.error(f"[OTPService] Redis rate-limit check failed: {e}")
            return

        if int(count) > limit:
            try:
                ttl = int(self._redis.redis.ttl(key)) or window_seconds
            except Exception:
                ttl = window_seconds
            logger.warning(
                f"[OTPService] Rate limit hit for {_mask_phone(phone)} on {prefix!r} "
                f"(count={count}, limit={limit}, retry_after={ttl}s)"
            )
            raise OTPRateLimitExceededException(retry_after_seconds=max(ttl, 1))

    async def send_otp(
        self,
        phone_number: str,
        channel: Literal["sms", "call", "whatsapp"] = "sms",
    ) -> dict:
        self._ensure_configured()
        self._enforce_rate_limit(
            self.SEND_RL_PREFIX,
            phone_number,
            settings.OTP_SEND_LIMIT_PER_PHONE,
            settings.OTP_SEND_WINDOW_SECONDS,
        )

        masked = _mask_phone(phone_number)
        logger.info(f"[OTPService] Sending OTP to {masked} via {channel}")

        try:
            verification = await asyncio.to_thread(
                self._client.verify.v2.services(self._service_sid).verifications.create,
                to=phone_number,
                channel=channel,
            )
        except TwilioRestException as e:
            logger.error(
                f"[OTPService] Twilio send error for {masked}: "
                f"code={e.code} status={e.status} msg={e.msg}"
            )
            # Hide Twilio's raw error code from the client; surface a stable message.
            raise OTPSendFailedException()
        except Exception as e:
            logger.error(f"[OTPService] Unexpected send error for {masked}: {e}")
            raise OTPSendFailedException()

        logger.info(
            f"[OTPService] OTP sent to {masked}: sid={verification.sid} "
            f"status={verification.status}"
        )
        return {
            "sid": verification.sid,
            "status": verification.status,
            "channel": channel,
        }

    async def verify_otp(self, phone_number: str, code: str) -> dict:
        self._ensure_configured()
        self._enforce_rate_limit(
            self.VERIFY_RL_PREFIX,
            phone_number,
            settings.OTP_VERIFY_LIMIT_PER_PHONE,
            settings.OTP_VERIFY_WINDOW_SECONDS,
        )

        masked = _mask_phone(phone_number)
        logger.info(f"[OTPService] Verifying OTP for {masked}")

        try:
            check = await asyncio.to_thread(
                self._client.verify.v2.services(
                    self._service_sid
                ).verification_checks.create,
                to=phone_number,
                code=code,
            )
        except TwilioRestException as e:
            # Twilio returns 404 when the code is consumed/expired and no pending verification exists.
            if e.status == 404:
                logger.info(f"[OTPService] No active verification for {masked}")
                raise OTPVerificationFailedException()
            logger.error(
                f"[OTPService] Twilio verify error for {masked}: "
                f"code={e.code} status={e.status} msg={e.msg}"
            )
            raise OTPVerificationFailedException()
        except Exception as e:
            logger.error(f"[OTPService] Unexpected verify error for {masked}: {e}")
            raise OTPVerificationFailedException()

        if check.status != "approved":
            logger.warning(
                f"[OTPService] Verification not approved for {masked}: status={check.status}"
            )
            raise OTPVerificationFailedException(twilio_status=check.status)

        logger.info(f"[OTPService] OTP verified for {masked}")
        # Once approved, the verify counter is no longer protecting anything for this code.
        try:
            self._redis.redis.delete(f"{self.VERIFY_RL_PREFIX}{phone_number}")
        except Exception:
            pass
        return {"status": check.status}
