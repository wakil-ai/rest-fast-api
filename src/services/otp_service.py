"""Provider-neutral, JWT-bound OTP challenges. Redis is a security boundary."""
import asyncio
import hashlib
import json
import secrets

from twilio.rest import Client

from redis.exceptions import RedisError, WatchError

from core.config import settings
from core.exceptions import OTPRateLimitExceededException, OTPSendFailedException, OTPServiceNotConfiguredException, OTPVerificationFailedException
from models.otp import SendOTPRequest
from services.eskiz_client import EskizClient
from services.local_otp_store import LocalOTPStore
from services.redis_service import RedisService


class OTPService:
    SEND_RL_PREFIX = 'otp:rl:send:'
    VERIFY_RL_PREFIX = 'otp:rl:verify:'

    def __init__(self, *, redis_client=None, config=None, eskiz_client=None, twilio_client=None):
        self._config = config or settings
        self._redis = redis_client if redis_client is not None else RedisService().redis
        c = self._config
        self._twilio = twilio_client
        if self._twilio is None and all((c.TWILIO_ACCOUNT_SID, c.TWILIO_AUTH_TOKEN, c.TWILIO_VERIFY_SERVICE_SID)):
            self._twilio = Client(c.TWILIO_ACCOUNT_SID, c.TWILIO_AUTH_TOKEN)
        self._store = LocalOTPStore(redis_client=self._redis, code_length=c.OTP_CODE_LENGTH, ttl_seconds=c.OTP_CODE_TTL_SECONDS, max_attempts=c.OTP_MAX_ATTEMPTS, hash_secret=c.OTP_HASH_SECRET or '')
        self._eskiz = eskiz_client
        if self._eskiz is None and all((c.ESKIZ_EMAIL, c.ESKIZ_PASSWORD, c.ESKIZ_SENDER_ID, c.ESKIZ_BASE_URL, c.OTP_HASH_SECRET)):
            self._eskiz = EskizClient(email=c.ESKIZ_EMAIL, password=c.ESKIZ_PASSWORD, sender_id=c.ESKIZ_SENDER_ID, base_url=c.ESKIZ_BASE_URL, timeout_seconds=c.ESKIZ_TIMEOUT_SECONDS)

    def _enforce_rate_limit(self, prefix, phone, limit, window_seconds):
        key = prefix + phone
        pipe = self._redis.pipeline()
        pipe.incr(key)
        pipe.expire(key, window_seconds, nx=True)
        count, _ = pipe.execute()
        if count > limit:
            raise OTPRateLimitExceededException(retry_after_seconds=max(self._redis.ttl(key), 1))

    async def send_otp(self, phone_number, channel='sms', *, locale='uz', user_id):
        phone_number = SendOTPRequest(phone_number=phone_number, channel=channel).phone_number
        provider = 'eskiz' if phone_number.startswith('+998') and channel == 'sms' else 'twilio'
        if provider == 'eskiz' and (self._eskiz is None or not self._config.OTP_HASH_SECRET):
            raise OTPServiceNotConfiguredException()
        if provider == 'twilio' and (self._twilio is None or not self._config.TWILIO_VERIFY_SERVICE_SID):
            raise OTPServiceNotConfiguredException()
        c = self._config
        lease = None
        try:
            lease, code = self._store.begin_send(phone_number, lease_seconds=int(c.ESKIZ_TIMEOUT_SECONDS * 6) + 60, cooldown_seconds=c.OTP_RESEND_COOLDOWN_SECONDS)
            self._enforce_rate_limit(self.SEND_RL_PREFIX, phone_number, c.OTP_SEND_LIMIT_PER_PHONE, c.OTP_SEND_WINDOW_SECONDS)
            if provider == 'eskiz':
                request_id = await self._eskiz.send_sms(phone_number, code, locale)
            else:
                try:
                    result = await asyncio.to_thread(self._twilio.verify.v2.services(c.TWILIO_VERIFY_SERVICE_SID).verifications.create, to=phone_number, channel=channel)
                    if result.status != 'pending':
                        raise OTPSendFailedException()
                    request_id = result.sid
                except Exception:
                    raise OTPSendFailedException() from None
            self._store.complete_send(phone_number, lease, code, user_id=user_id, provider=provider, channel=channel, cooldown_seconds=c.OTP_RESEND_COOLDOWN_SECONDS)
            return {'status': 'pending', 'channel': channel, 'request_id': request_id, 'expires_in': c.OTP_CODE_TTL_SECONDS, 'resend_after': c.OTP_RESEND_COOLDOWN_SECONDS}
        except RedisError:
            raise OTPServiceNotConfiguredException() from None
        finally:
            if lease is not None:
                try:
                    self._store.cancel_send(phone_number, lease)
                except RedisError:
                    pass  # leases always expire; never turn an outage into a send

    async def verify_otp(self, phone_number, code, *, user_id):
        phone_number = SendOTPRequest(phone_number=phone_number).phone_number
        c = self._config
        try:
            self._enforce_rate_limit(self.VERIFY_RL_PREFIX, phone_number, c.OTP_VERIFY_LIMIT_PER_PHONE, c.OTP_VERIFY_WINDOW_SECONDS)
            raw = self._redis.get(self._store._key(phone_number))
            payload = json.loads(raw) if raw else {}
            if payload.get('provider') == 'twilio':
                if self._twilio is None or not c.TWILIO_VERIFY_SERVICE_SID:
                    raise OTPServiceNotConfiguredException()
                lease = self._store.begin_provider_verify(phone_number, user_id, lease_seconds=120)
                if lease is None:
                    raise OTPVerificationFailedException()
                approved = False
                try:
                    check = await asyncio.to_thread(self._twilio.verify.v2.services(c.TWILIO_VERIFY_SERVICE_SID).verification_checks.create, to=phone_number, code=code)
                    approved = check.status == 'approved'
                except Exception:
                    raise OTPVerificationFailedException() from None
                finally:
                    approved = self._store.finish_provider_verify(phone_number, lease, approved)
            else:
                approved = self._store.verify(phone_number, code, user_id=user_id)
            if not approved:
                raise OTPVerificationFailedException()
            token = secrets.token_urlsafe(32)
            self._redis.set(self._proof_key(token), json.dumps({'user_id': user_id, 'phone_number': phone_number}), ex=c.OTP_PROOF_TTL_SECONDS)
            return {'status': 'approved', 'verification_token': token}
        except RedisError:
            raise OTPServiceNotConfiguredException() from None

    @staticmethod
    def _proof_key(token):
        return 'otp:proof:' + hashlib.sha256(token.encode()).hexdigest()

    def consume_phone_proof(self, user_id, phone_number, token):
        phone_number = SendOTPRequest(phone_number=phone_number).phone_number
        key = self._proof_key(token)
        try:
            for _ in range(5):
                with self._redis.pipeline() as pipe:
                    try:
                        pipe.watch(key)
                        raw = pipe.get(key)
                        if raw is None or json.loads(raw) != {'user_id': user_id, 'phone_number': phone_number}:
                            return False
                        pipe.multi()
                        pipe.delete(key)
                        pipe.execute()
                        return True
                    except WatchError:
                        continue
            raise OTPServiceNotConfiguredException()
        except RedisError:
            raise OTPServiceNotConfiguredException() from None
