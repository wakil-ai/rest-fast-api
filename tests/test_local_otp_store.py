import json
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier, local

from fakeredis import FakeRedis as RedisFake

from services.local_otp_store import LocalOTPStore
import pytest


def make_store(redis):
    return LocalOTPStore(redis_client=redis, code_length=6, ttl_seconds=300, max_attempts=3, hash_secret='test-secret')


def test_staged_send_preserves_old_code_until_success():
    redis = FakeRedis()
    store = make_store(redis)
    old = store.issue('+998901234567')
    lease, code = store.begin_send('+998901234567', lease_seconds=120, cooldown_seconds=60)
    assert code != old
    assert store.verify('+998901234567', old) is False  # block verification in flight
    store.cancel_send('+998901234567', lease)
    assert store.verify('+998901234567', old) is True
    lease, code = store.begin_send('+998901234567', lease_seconds=120, cooldown_seconds=60)
    store.complete_send('+998901234567', lease, code, user_id='user1', provider='eskiz', channel='sms', cooldown_seconds=60)
    assert store.verify('+998901234567', code, user_id='user2') is False
    assert store.verify('+998901234567', code, user_id='user1') is True


def test_resend_differs_even_after_previous_code_was_consumed(monkeypatch):
    redis = FakeRedis()
    store = make_store(redis)
    monkeypatch.setattr('services.local_otp_store.secrets.randbelow', lambda _: 0)
    phone = '+998901234567'
    lease, first = store.begin_send(phone, lease_seconds=120, cooldown_seconds=60)
    store.complete_send(phone, lease, first, user_id='user1', provider='eskiz', channel='sms', cooldown_seconds=60)
    assert store.verify(phone, first, user_id='user1')
    redis.delete('otp:cooldown:' + phone)
    _, second = store.begin_send(phone, lease_seconds=120, cooldown_seconds=60)
    assert second != first


def test_provider_verification_cannot_claim_a_local_sms_challenge():
    redis = FakeRedis()
    store = make_store(redis)
    phone = '+998901234567'
    lease, code = store.begin_send(phone, lease_seconds=120, cooldown_seconds=60)
    store.complete_send(phone, lease, code, user_id='user1', provider='eskiz', channel='sms', cooldown_seconds=60)
    assert store.begin_provider_verify(phone, 'user1', lease_seconds=120) is None
    assert store.verify(phone, code, user_id='user1')


def test_local_verification_cannot_approve_twilio_placeholder_code():
    redis = FakeRedis()
    store = make_store(redis)
    phone = '+998901234567'
    lease, code = store.begin_send(phone, lease_seconds=120, cooldown_seconds=60)
    store.complete_send(phone, lease, code, user_id='user1', provider='twilio', channel='call', cooldown_seconds=60)
    assert store.verify(phone, code, user_id='user1') is False


class FakeRedis(RedisFake):
    def __init__(self) -> None:
        super().__init__(decode_responses=True)

    @property
    def ttls(self) -> dict[str, int]:
        return {
            key: self.ttl(key)
            for key in self.scan_iter()
        }



def test_issued_code_is_hashed_and_can_be_verified_once() -> None:
    redis = FakeRedis()
    store = LocalOTPStore(
        redis_client=redis,
        code_length=6,
        ttl_seconds=300,
        max_attempts=3,
        hash_secret="test-otp-hash-secret",
    )

    phone_number = "+998901234567"
    redis_key = f"otp:code:{phone_number}"

    code = store.issue(phone_number)

    assert code.isdigit()
    assert len(code) == 6
    assert redis.ttls[redis_key] == 300

    stored_payload = redis.get(redis_key)

    assert stored_payload is not None
    assert code not in stored_payload
    assert json.loads(stored_payload)["attempts_remaining"] == 3

    assert store.verify(phone_number, code) is True
    assert redis.get(redis_key) is None

def test_wrong_code_decrements_attempts_without_consuming_valid_code() -> None:
    redis = FakeRedis()
    store = LocalOTPStore(
        redis_client=redis,
        code_length=6,
        ttl_seconds=300,
        max_attempts=3,
        hash_secret="test-otp-hash-secret",
    )
    phone_number = "+998951234567"
    redis_key = f"otp:code:{phone_number}"

    code = store.issue(phone_number)

    wrong_code = "000000" if code != "000000" else "111111"

    assert store.verify(phone_number, wrong_code) is False

    stored_payload = redis.get(redis_key)

    assert stored_payload is not None
    assert json.loads(stored_payload)["attempts_remaining"] == 2
    assert store.verify(phone_number, code) is True


def test_exhausted_attempts_invalidate_the_code() -> None:
    redis = FakeRedis()
    store = LocalOTPStore(
        redis_client=redis,
        code_length=6,
        ttl_seconds=300,
        max_attempts=3,
        hash_secret="test-otp-hash-secret",
    )
    phone_number = "+998901234567"
    redis_key = f"otp:code:{phone_number}"

    code = store.issue(phone_number)
    wrong_code = "000000" if code != "000000" else "111111"

    for _ in range(3):
        assert store.verify(phone_number, wrong_code) is False

    assert redis.get(redis_key) is None
    assert store.verify(phone_number, code) is False


def test_concurrent_verification_accepts_code_only_once(monkeypatch) -> None:
    redis = FakeRedis()
    store = LocalOTPStore(
        redis_client=redis,
        code_length=6,
        ttl_seconds=300,
        max_attempts=3,
        hash_secret="test-otp-hash-secret",
    )
    phone_number = "+998901234567"
    code = store.issue(phone_number)

    barrier = Barrier(2)
    thread_state = local()
    original_hash_code = store._hash_code

    def synchronized_hash_code(phone: str, submitted_code: str) -> str:
        result = original_hash_code(phone, submitted_code)
        if not getattr(thread_state, "synchronized", False):
            thread_state.synchronized = True
            barrier.wait(timeout=5)
        return result

    monkeypatch.setattr(store, "_hash_code", synchronized_hash_code)

    with ThreadPoolExecutor(max_workers=2) as executor:
        futures = [
            executor.submit(store.verify, phone_number, code)
            for _ in range(2)
        ]
        results = [future.result(timeout=10) for future in futures]

    assert sorted(results) == [False, True]
