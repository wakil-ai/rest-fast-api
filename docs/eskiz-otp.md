# WK-110: phone verification

## Provider strategy and prerequisites

`+998` SMS uses Eskiz; international SMS and `call`/`whatsapp` retain Twilio
Verify. Missing credentials for the selected provider return 503; there is no
silent cross-provider fallback. Configure the environment variables in
`.env.example`. Eskiz requires `OTP_HASH_SECRET` (at least 16 characters; use a
long random secret), account email/password, approved sender and base URL.
Secrets are never included in responses or application logs. Provision approved
SMS templates matching the Uzbek/Russian/English messages before enabling traffic.
Provider acknowledgement is acceptance/queueing, not proof of SMS delivery.

The primary provider contract was read directly from Eskiz's published Postman
collection (no authenticated/live provider requests):
https://documenter.getpostman.com/view/663428/TVK5eMco
https://documenter.gw.postman.com/api/collections/663428/TVK5eMco

Verified request shapes: multipart `POST /api/auth/login` with `email` and
`password`, token at `data.token`; Bearer `PATCH /api/auth/refresh`, replacement
at `data.token`; multipart `POST /api/message/sms/send` with `mobile_phone`
(digits without `+`), `message`, `from`. The queued response contains string
`id` and `status: waiting`. A 401 permits one refresh and one SMS retry; a 401
from refresh permits one re-login instead. Timeouts, malformed responses, other
errors and a second SMS 401 never trigger an automatic SMS resend.

## Browser contract

All three operations require a user Bearer JWT, including send and verify.
OAuth must complete first. Service keys alone do **not** authorize these routes.
This intentionally breaks unauthenticated legacy OTP/phone PATCH clients; update
them to the new contract. There is no service-key exception to phone PATCH.
Super-admin user creation retains its existing server-trusted initial-phone
integration; its secret must never be exposed to a browser.

1. `POST /api/v2/auth/otp/send`
   `{phone_number, channel: "sms", locale: "uz" | "ru" | "en"}`
   returns `{success: true, status: "pending", channel: "sms", request_id,
   expires_in: 300, resend_after: 60}` with default settings.
2. `POST /api/v2/auth/otp/verify` `{phone_number, code}` returns
   `{success: true, status: "approved", phone_number, verification_token}`.
3. `PATCH /api/v2/history/users/phone-number`
   `{user_id, phone_number, verification_token}` requires `user_id` equal to the
   JWT subject. The proof must match that subject and normalized E.164 phone.
   It is atomically consumed once before Mongo persistence. Generic profile
   updates are restricted to username/first_name/last_name/picture, so they
   cannot bypass this proof.

Nuxt can verify inside its `/api/auth/phone` handler and immediately pass the
proof to backend PATCH using the same user JWT. Never trust a browser-supplied
user ID. An invalid/expired/replayed proof returns 400, mismatched JWT subject
403, absent JWT 401, missing proof 422. If Mongo persistence fails after proof
consumption, verify a fresh code rather than replaying the consumed proof.

## Lifecycle/security

Redis must be available: send, rate limiting, verify and proof consumption all
fail closed with 503 on Redis errors. Per-phone atomic reservations block
concurrent sends and verification during a send. Reservations have finite TTLs;
an expired lease cannot publish a late challenge. Failed sends preserve the old
challenge and release their reservation/cooldown when Redis is reachable.

Successful Eskiz resends after the 60-second cooldown generate a different code,
replace the old challenge only after provider acceptance, refresh its 300-second
TTL, and reset only the per-code attempt budget. Codes are HMAC digests, never
plaintext in Redis. A last-code digest is retained separately after expiry or
consumption to prevent immediate code reuse (remove it only when intentionally
resetting phone OTP state). Twilio controls its own code generation and may reuse
its code during a provider verification lifetime; backend cannot guarantee a
new Twilio code without Twilio custom-code configuration. This difference does
not affect the Eskiz SMS flow.

Existing hourly limits remain: 3 send attempts and 10 verification attempts per
phone. Provider failures consume hourly send quota; cooldown-rejected sends do
not. Approval does not clear hourly verify quota. All new challenges are bound
to the initiating JWT subject. Proofs are random, hashed-key Redis entries,
expire after 300 seconds by default, and cannot be consumed for another user or
phone. WATCH transactions prevent proof replay and duplicate approvals.

Redis contains phone identifiers in keys; restrict access and use appropriate
transport/storage protection. Logs never include raw phone/code/credentials or
provider response bodies. No real SMS or deployment was performed.

## Isolated verification

Use the repository `.venv` and installed requirements (including the existing
`fakeredis==2.39.0` addition). HTTP provider boundaries use `httpx.MockTransport`;
Redis logic uses fakeredis with real WATCH/MULTI semantics, not fabricated
responses. Run:

```sh
env TELEGRAM_BOT_TOKEN=test-only TELEGRAM_BOT_LOGIN=test-only \
  PAYME_MERCHANT_ID=test-only PAYME_MERCHANT_KEY=test-only \
  .venv/bin/python -m pytest tests/test_local_otp_store.py \
  tests/test_eskiz_client.py tests/test_otp_service.py tests/test_otp_routes.py -q
```

The broad suite currently cannot collect `tests/test_jwt_auth.py`: importing
`main` constructs GCS StorageService without Application Default Credentials.
Do not invent cloud credentials to unblock this. Real provider delivery,
approved templates, account billing, Redis deployment and frontend integration
need a separately authorized staging verification; unit tests do not prove them.
