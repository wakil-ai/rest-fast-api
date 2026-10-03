from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from fakeredis import FakeRedis
from services.otp_service import OTPService

PHONE = '+998901234567'


def config(**overrides):
    return SimpleNamespace(TWILIO_ACCOUNT_SID=None, TWILIO_AUTH_TOKEN=None, TWILIO_VERIFY_SERVICE_SID=None, ESKIZ_EMAIL='test@example.invalid', ESKIZ_PASSWORD='test-only', ESKIZ_SENDER_ID='4546', ESKIZ_BASE_URL='https://notify.eskiz.uz', ESKIZ_TIMEOUT_SECONDS=5, OTP_HASH_SECRET='test-hash-secret', OTP_CODE_LENGTH=6, OTP_CODE_TTL_SECONDS=300, OTP_MAX_ATTEMPTS=3, OTP_RESEND_COOLDOWN_SECONDS=60, OTP_SEND_LIMIT_PER_PHONE=3, OTP_SEND_WINDOW_SECONDS=3600, OTP_VERIFY_LIMIT_PER_PHONE=10, OTP_VERIFY_WINDOW_SECONDS=3600, OTP_PROOF_TTL_SECONDS=300, **overrides)


@pytest.mark.asyncio
async def test_uzbekistan_sms_returns_neutral_pending_then_user_bound_proof():
    redis = FakeRedis(decode_responses=True)
    eskiz = SimpleNamespace(send_sms=AsyncMock(return_value='provider-id'))
    service = OTPService(redis_client=redis, config=config(), eskiz_client=eskiz)
    result = await service.send_otp(PHONE, user_id='user1', locale='uz')
    assert result == {'status': 'pending', 'channel': 'sms', 'request_id': 'provider-id', 'expires_in': 300, 'resend_after': 60}
    code = eskiz.send_sms.call_args.args[1]
    assert code not in redis.get('otp:code:' + PHONE)
    verified = await service.verify_otp(PHONE, code, user_id='user1')
    assert verified['status'] == 'approved'
    proof = verified['verification_token']
    assert proof
    assert service.consume_phone_proof('user2', PHONE, proof) is False
    assert service.consume_phone_proof('user1', '+998 90 123 45 67', proof) is True
    assert service.consume_phone_proof('user1', PHONE, proof) is False


@pytest.mark.asyncio
@pytest.mark.parametrize('phone,channel', [('+12025550123', 'sms'), (PHONE, 'call'), (PHONE, 'whatsapp')])
async def test_international_and_non_sms_keep_twilio_verification(phone, channel):
    from unittest.mock import MagicMock
    twilio = MagicMock()
    twilio.verify.v2.services.return_value.verifications.create.return_value = SimpleNamespace(sid='twilio-id', status='pending')
    twilio.verify.v2.services.return_value.verification_checks.create.return_value = SimpleNamespace(status='approved')
    redis = FakeRedis(decode_responses=True)
    eskiz = SimpleNamespace(send_sms=AsyncMock())
    c = config()
    c.TWILIO_VERIFY_SERVICE_SID = 'test-service'
    service = OTPService(redis_client=redis, config=c, eskiz_client=eskiz, twilio_client=twilio)
    result = await service.send_otp(phone, channel, user_id='user1')
    assert result['request_id'] == 'twilio-id'
    eskiz.send_sms.assert_not_called()
    assert (await service.verify_otp(phone, '123456', user_id='user1'))['status'] == 'approved'
    assert redis.get('otp:rl:verify:' + phone) == '1'  # approval never resets hourly abuse cap


@pytest.fixture
def otp_system():
    redis = FakeRedis(decode_responses=True)
    eskiz = SimpleNamespace(send_sms=AsyncMock(return_value='provider-id'))
    return OTPService(redis_client=redis, config=config(), eskiz_client=eskiz), redis, eskiz


@pytest.mark.asyncio
async def test_resend_cooldown_then_replacement_and_fresh_ttl(otp_system):
    from core.exceptions import OTPRateLimitExceededException, OTPVerificationFailedException
    service, redis, eskiz = otp_system
    await service.send_otp(PHONE, user_id='user1')
    old = eskiz.send_sms.call_args.args[1]
    with pytest.raises(OTPRateLimitExceededException) as error:
        await service.send_otp(PHONE, user_id='user1')
    assert 1 <= error.value.retry_after_seconds <= 60
    assert eskiz.send_sms.await_count == 1
    redis.delete('otp:cooldown:' + PHONE)  # advance past cooldown
    redis.expire('otp:code:' + PHONE, 10)
    await service.send_otp(PHONE, user_id='user1')
    new = eskiz.send_sms.call_args.args[1]
    assert new != old
    assert redis.ttl('otp:code:' + PHONE) == 300
    with pytest.raises(OTPVerificationFailedException):
        await service.verify_otp(PHONE, old, user_id='user1')
    assert (await service.verify_otp(PHONE, new, user_id='user1'))['status'] == 'approved'


@pytest.mark.asyncio
async def test_failed_send_preserves_prior_code_and_releases_lease(otp_system):
    from core.exceptions import OTPSendFailedException
    service, redis, eskiz = otp_system
    await service.send_otp(PHONE, user_id='user1')
    old = eskiz.send_sms.call_args.args[1]
    redis.delete('otp:cooldown:' + PHONE)
    eskiz.send_sms.side_effect = OTPSendFailedException()
    with pytest.raises(OTPSendFailedException):
        await service.send_otp(PHONE, user_id='user1')
    assert not redis.exists('otp:lock:' + PHONE, 'otp:cooldown:' + PHONE)
    assert (await service.verify_otp(PHONE, old, user_id='user1'))['status'] == 'approved'


@pytest.mark.asyncio
async def test_hourly_send_cap_survives_cooldown_and_success(otp_system):
    from core.exceptions import OTPRateLimitExceededException
    service, redis, eskiz = otp_system
    for _ in range(3):
        await service.send_otp(PHONE, user_id='user1')
        redis.delete('otp:cooldown:' + PHONE)
    with pytest.raises(OTPRateLimitExceededException):
        await service.send_otp(PHONE, user_id='user1')
    assert eskiz.send_sms.await_count == 3
    assert not redis.exists('otp:lock:' + PHONE)
    assert 3590 < redis.ttl('otp:rl:send:' + PHONE) <= 3600


@pytest.mark.asyncio
async def test_hourly_verify_cap_is_not_reset_by_success(otp_system):
    from core.exceptions import OTPRateLimitExceededException
    service, redis, eskiz = otp_system
    await service.send_otp(PHONE, user_id='user1')
    code = eskiz.send_sms.call_args.args[1]
    redis.set('otp:rl:verify:' + PHONE, 9, ex=3600)
    await service.verify_otp(PHONE, code, user_id='user1')
    with pytest.raises(OTPRateLimitExceededException):
        await service.verify_otp(PHONE, code, user_id='user1')


@pytest.mark.asyncio
async def test_local_attempt_cap_and_expiry_do_not_mint_proofs(otp_system):
    from core.exceptions import OTPVerificationFailedException
    service, redis, eskiz = otp_system
    await service.send_otp(PHONE, user_id='user1')
    code = eskiz.send_sms.call_args.args[1]
    wrong = '111111' if code != '111111' else '222222'
    for _ in range(3):
        with pytest.raises(OTPVerificationFailedException):
            await service.verify_otp(PHONE, wrong, user_id='user1')
    with pytest.raises(OTPVerificationFailedException):
        await service.verify_otp(PHONE, code, user_id='user1')
    assert not list(redis.scan_iter('otp:proof:*'))
    redis.delete('otp:cooldown:' + PHONE)
    await service.send_otp(PHONE, user_id='user1')
    redis.expire('otp:code:' + PHONE, 0)
    with pytest.raises(OTPVerificationFailedException):
        await service.verify_otp(PHONE, eskiz.send_sms.call_args.args[1], user_id='user1')


@pytest.mark.asyncio
@pytest.mark.parametrize('operation', ['send', 'verify', 'proof'])
async def test_redis_outage_fails_closed_without_provider_calls(otp_system, monkeypatch, operation):
    from redis.exceptions import ConnectionError
    from core.exceptions import OTPServiceNotConfiguredException
    service, redis, eskiz = otp_system
    def unavailable(*args, **kwargs):
        raise ConnectionError('private-provider-error-body')
    monkeypatch.setattr(redis, 'pipeline', unavailable)
    with pytest.raises(OTPServiceNotConfiguredException) as error:
        if operation == 'send':
            await service.send_otp(PHONE, user_id='user1')
        elif operation == 'verify':
            await service.verify_otp(PHONE, '123456', user_id='user1')
        else:
            service.consume_phone_proof('user1', PHONE, 'proof')
    assert error.value.status_code == 503
    assert 'private-provider-error-body' not in str(error.value)
    eskiz.send_sms.assert_not_called()


@pytest.mark.asyncio
async def test_redis_outage_after_sms_cannot_publish_success(otp_system, monkeypatch):
    from redis.exceptions import ConnectionError
    from core.exceptions import OTPServiceNotConfiguredException
    service, redis, eskiz = otp_system
    async def accepted(*args):
        monkeypatch.setattr(redis, 'pipeline', lambda: (_ for _ in ()).throw(ConnectionError()))
        return 'accepted'
    eskiz.send_sms.side_effect = accepted
    with pytest.raises(OTPServiceNotConfiguredException):
        await service.send_otp(PHONE, user_id='user1')
    assert redis.get('otp:code:' + PHONE) is None
    assert 0 < redis.ttl('otp:lock:' + PHONE) <= 90


@pytest.mark.asyncio
async def test_missing_hash_secret_never_sends_local_sms(otp_system):
    from core.exceptions import OTPServiceNotConfiguredException
    service, redis, eskiz = otp_system
    service._config.OTP_HASH_SECRET = None
    with pytest.raises(OTPServiceNotConfiguredException):
        await service.send_otp(PHONE, user_id='user1')
    eskiz.send_sms.assert_not_called()
