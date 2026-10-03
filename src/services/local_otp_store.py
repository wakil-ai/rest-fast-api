import hashlib
import hmac
import json
import secrets

from redis.exceptions import WatchError

class LocalOTPStore:
    def __init__(
        self,
        *,
        redis_client,
        code_length: int,
        ttl_seconds: int,
        max_attempts: int,
        hash_secret: str,
    ) -> None:
        self._redis = redis_client
        self._code_length = code_length
        self._ttl_seconds = ttl_seconds
        self._max_attempts = max_attempts
        self._hash_secret = hash_secret.encode("utf-8")

    def _key(self, phone_number: str) -> str:
        return f"otp:code:{phone_number}"

    def _hash_code(self, phone_number: str, code: str) -> str:
        message = f"{phone_number}:{code}".encode("utf-8")
        return hmac.new(
            self._hash_secret,
            message,
            hashlib.sha256
        ).hexdigest()

    def issue(self, phone_number: str) -> str:
        code = "".join(
            secrets.choice("0123456789")
            for _ in range(self._code_length)
        )
        payload = {
            "code_hash": self._hash_code(phone_number, code),
            "attempts_remaining": self._max_attempts,
        }
        self._redis.set(
            self._key(phone_number),
            json.dumps(payload),
            ex=self._ttl_seconds,
        )
        return code

    def begin_send(self, phone_number, *, lease_seconds, cooldown_seconds):
        from core.exceptions import OTPRateLimitExceededException

        lock, cooldown = f'otp:lock:{phone_number}', f'otp:cooldown:{phone_number}'
        for _ in range(5):
            with self._redis.pipeline() as pipe:
                try:
                    pipe.watch(lock, cooldown, self._key(phone_number))
                    if pipe.exists(lock) or pipe.exists(cooldown):
                        raise OTPRateLimitExceededException(retry_after_seconds=max(pipe.ttl(lock), pipe.ttl(cooldown), 1))
                    old = pipe.get(self._key(phone_number))
                    old_hash = pipe.get(f'otp:last:{phone_number}') or (json.loads(old)['code_hash'] if old else None)
                    # A bounded alternative even if the random generator repeats.
                    number = secrets.randbelow(10 ** self._code_length)
                    code = str(number).zfill(self._code_length)
                    if old_hash and hmac.compare_digest(old_hash, self._hash_code(phone_number, code)):
                        code = str((number + 1) % (10 ** self._code_length)).zfill(self._code_length)
                    lease = secrets.token_urlsafe(32)
                    pipe.multi()
                    pipe.set(lock, lease, ex=lease_seconds)
                    pipe.set(cooldown, lease, ex=cooldown_seconds)
                    pipe.execute()
                    return lease, code
                except WatchError:
                    continue
        raise WatchError('OTP send contention')

    def complete_send(self, phone_number, lease, code, *, user_id, provider, channel, cooldown_seconds):
        lock = f'otp:lock:{phone_number}'
        with self._redis.pipeline() as pipe:
            pipe.watch(lock)
            if pipe.get(lock) != lease:
                raise WatchError('OTP send lease expired')
            payload = {'code_hash': self._hash_code(phone_number, code), 'attempts_remaining': self._max_attempts, 'user_id': user_id, 'provider': provider, 'channel': channel}
            pipe.multi()
            pipe.set(self._key(phone_number), json.dumps(payload), ex=self._ttl_seconds)
            # Keep only the previous digest after expiry/consumption to forbid reuse.
            pipe.set(f'otp:last:{phone_number}', payload['code_hash'])
            pipe.set(f'otp:cooldown:{phone_number}', lease, ex=cooldown_seconds)
            pipe.delete(lock)
            pipe.execute()

    def cancel_send(self, phone_number, lease):
        lock = f'otp:lock:{phone_number}'
        with self._redis.pipeline() as pipe:
            pipe.watch(lock)
            if pipe.get(lock) != lease:
                return
            pipe.multi()
            pipe.delete(lock, f'otp:cooldown:{phone_number}')
            pipe.execute()

    def begin_provider_verify(self, phone_number, user_id, *, lease_seconds):
        key, lock = self._key(phone_number), f'otp:lock:{phone_number}'
        for _ in range(5):
            with self._redis.pipeline() as pipe:
                try:
                    pipe.watch(key, lock)
                    raw = pipe.get(key)
                    if not raw or pipe.exists(lock):
                        return None
                    payload = json.loads(raw)
                    if payload.get('provider') != 'twilio' or payload.get('user_id') != user_id or payload['attempts_remaining'] <= 0:
                        return None
                    payload['attempts_remaining'] -= 1
                    lease = secrets.token_urlsafe(32)
                    pipe.multi()
                    pipe.set(key, json.dumps(payload), keepttl=True)
                    pipe.set(lock, lease, ex=lease_seconds)
                    pipe.execute()
                    return lease
                except WatchError:
                    continue
        raise WatchError('OTP verification contention')

    def finish_provider_verify(self, phone_number, lease, approved):
        key, lock = self._key(phone_number), f'otp:lock:{phone_number}'
        with self._redis.pipeline() as pipe:
            pipe.watch(key, lock)
            if pipe.get(lock) != lease:
                raise WatchError('OTP verification lease expired')
            raw = pipe.get(key)
            if not raw:
                approved = False
            pipe.multi()
            pipe.delete(lock)
            if approved or (raw and json.loads(raw)['attempts_remaining'] <= 0):
                pipe.delete(key)
            pipe.execute()
            return approved

    def verify(self, phone_number: str, code: str, *, user_id=None) -> bool:
        key = self._key(phone_number)
        lock = f'otp:lock:{phone_number}'

        for _ in range(5):
            with self._redis.pipeline() as pipe:
                try:
                    pipe.watch(key, lock)
                    if pipe.exists(lock):
                        return False
                    stored_payload = pipe.get(key)

                    if stored_payload is None:
                        return False

                    payload = json.loads(stored_payload)
                    if user_id is not None and (payload.get('user_id') != user_id or payload.get('provider') != 'eskiz'):
                        return False
                    submitted_hash = self._hash_code(phone_number, code)
                    approved = (
                        payload["attempts_remaining"] > 0
                        and hmac.compare_digest(
                            payload["code_hash"],
                            submitted_hash,
                        )
                    )

                    if not approved:
                        payload["attempts_remaining"] -= 1

                    pipe.multi()
                    if approved or payload["attempts_remaining"] <= 0:
                        pipe.delete(key)
                    else:
                        pipe.set(
                            key,
                            json.dumps(payload),
                            keepttl=True,
                        )
                    pipe.execute()
                    return approved
                except WatchError:
                    continue

        raise WatchError("OTP verification contention; retry the request")