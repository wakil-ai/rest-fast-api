# Uzum Bank Merchant API — wakil.ai integration

This document describes how the Uzum Bank Merchant API is implemented on the wakil.ai
backend and how the Uzum team can test it manually via Postman.

## Endpoints

All five webhooks live under `/api/v2/transaction/uzum/`:

| Method | Path | Purpose |
|---|---|---|
| POST | `/api/v2/transaction/uzum/check`   | Verify payment is possible |
| POST | `/api/v2/transaction/uzum/create`  | Create a payment transaction |
| POST | `/api/v2/transaction/uzum/confirm` | Confirm transaction after funds debited |
| POST | `/api/v2/transaction/uzum/reverse` | Cancel transaction |
| POST | `/api/v2/transaction/uzum/status`  | Check transaction status |

Production base URL: `https://api.wakil.ai`
Staging base URL: *(TBD — share with Uzum team once deployed)*

All requests must:

- Use HTTPS
- Send `Content-Type: application/json`
- Send `Authorization: Basic base64(username:password)` — credentials shared out of band

## Authentication

`Authorization: Basic <base64(username:password)>` on every request.

Username and password are configured via env vars on our side (`UZUM_USERNAME`,
`UZUM_PASSWORD`). For the initial test phase the wakil.ai team picks the values and
shares them with the Uzum team; Uzum will then use them when calling our endpoints
from Postman. For production, swap to credentials assigned by Uzum.

## Service ID

A single `UZUM_SERVICE_ID` configured on our side. Every request from Uzum must
include this exact value in `serviceId`. If it does not match, we respond with
errorCode `10006`.

## Plan / amount model

wakil.ai has 7 subscription plans with fixed prices. The customer in the Uzum Bank
app enters **two fields** in Uzum's catalog UI, which arrive in `params`:

| Field key (any accepted) | What it is | Example |
|---|---|---|
| `userId` (also `user_id`, `account`) | wakil.ai user identifier | `1163985408800857144` |
| `planId` (also `plan_id`, `tariff`) | Plan code `<tier>_<period>` | `standard_monthly` |

The customer does **not** type an amount. Instead, Uzum's catalog UI calls our
`/check` with the chosen `userId` + `planId`, and we return the price under
`data.amount.value` (in sums, see `/check` example below). Uzum displays that
amount to the customer for confirmation, then `/create` is called with the same
amount in tiyin.

Valid `planId` values:

| planId | Price (SUM) | Price (tiyin) |
|---|---:|---:|
| `basic_daily`       | 15 000     | 1 500 000     |
| `standard_daily`    | 30 000     | 3 000 000     |
| `premium_daily`     | 50 000     | 5 000 000     |
| `standard_monthly`  | 300 000    | 30 000 000    |
| `standard_yearly`   | 3 000 000  | 300 000 000   |
| `pro_monthly`       | 600 000    | 60 000 000    |
| `pro_yearly`        | 6 000 000  | 600 000 000   |

(Prices are taken from the `PAYME_SUBSCRIPTION_*_PRICE_SUM` env vars and shared across
all payment providers. The values above are the production defaults. Legacy
`daily_daily` is still accepted as an alias for `basic_daily`.)

In `/create`, we validate that `amount` (sent by Uzum in tiyin) equals the configured
plan price × 100. Mismatch → errorCode `10007`.

## State machine

```
        /create               /confirm
NONE --------------> CREATED --------------> CONFIRMED
                       |
                       +-------- /reverse --> REVERSED
                                              ^
                                              | (also reachable from CONFIRMED
                                              |  — manual review, sub not auto-revoked)
```

`/confirm` is idempotent — Uzum may retry it up to 10 times on 5xx/timeout. The first
successful confirmation grants the subscription; subsequent retries just return the
same `CONFIRMED` body.

## Request / response shapes

Below: minimal examples. Field types and required fields match the Uzum Merchant API
v1.0.0 spec — see https://developer.uzumbank.uz/en/merchant/ for the full schema.

### /check

Request:

```json
{
  "serviceId": 101202,
  "timestamp": 1779000000000,
  "params": {
    "userId": "1163985408800857144",
    "planId": "standard_monthly"
  }
}
```

Response 200 OK:

```json
{
  "serviceId": 101202,
  "timestamp": 1779000000001,
  "status": "OK",
  "data": {
    "account": { "value": "1163985408800857144" },
    "tariff":  { "value": "standard_monthly" },
    "amount":  { "value": "300000" }
  }
}
```

> **Note on `data.amount.value`:** This is the price for the chosen `planId`, in
> **sums** (not tiyin), serialized as a string. Uzum displays this to the
> customer for confirmation; the customer does not type an amount themselves.
> Every other `amount` field in this protocol (in `/create`, `/confirm`,
> `/reverse`, `/status`) is in tiyin per spec — `/check` is the only exception.

Response 400 (example — unknown plan):

```json
{
  "serviceId": 101202,
  "timestamp": 1779000000001,
  "status": "FAILED",
  "errorCode": "10007"
}
```

### /create

Request:

```json
{
  "serviceId": 101202,
  "timestamp": 1779000000000,
  "transId":  "8a7c3a02-3b41-4eb1-aa50-2b81e9c2d101",
  "amount":   30000000,
  "params":   { "userId": "1163985408800857144", "planId": "standard_monthly" }
}
```

Response 200 OK:

```json
{
  "serviceId": 101202,
  "transId":   "8a7c3a02-3b41-4eb1-aa50-2b81e9c2d101",
  "status":    "CREATED",
  "transTime": 1779000000050,
  "amount":    30000000,
  "data":      { "account": { "value": "..." }, "tariff": { "value": "..." } }
}
```

### /confirm

Request:

```json
{
  "serviceId":               101202,
  "timestamp":               1779000001000,
  "transId":                 "8a7c3a02-3b41-4eb1-aa50-2b81e9c2d101",
  "paymentSource":           "UZCARD",
  "phone":                   "+998901234567",
  "cardType":                2,
  "processingReferenceNumber": "634122000000000123",
  "tariff":                  null
}
```

Response 200 OK:

```json
{
  "serviceId":   101202,
  "transId":     "8a7c3a02-3b41-4eb1-aa50-2b81e9c2d101",
  "status":      "CONFIRMED",
  "confirmTime": 1779000001050,
  "amount":      30000000,
  "data":        { "account": { "value": "..." }, "tariff": { "value": "..." } }
}
```

### /reverse

Request:

```json
{ "serviceId": 101202, "timestamp": 1779000002000, "transId": "8a7c3a02-..." }
```

Response 200 OK:

```json
{
  "serviceId":   101202,
  "transId":     "8a7c3a02-...",
  "status":      "REVERSED",
  "reverseTime": 1779000002050,
  "amount":      30000000,
  "data":        { "account": { "value": "..." }, "tariff": { "value": "..." } }
}
```

### /status

Request:

```json
{ "serviceId": 101202, "timestamp": 1779000003000, "transId": "8a7c3a02-..." }
```

Response 200 OK (transaction is confirmed):

```json
{
  "serviceId":   101202,
  "transId":     "8a7c3a02-...",
  "status":      "CONFIRMED",
  "transTime":   1779000000050,
  "confirmTime": 1779000001050,
  "reverseTime": null,
  "amount":      30000000,
  "data":        { "account": { "value": "..." }, "tariff": { "value": "..." } }
}
```

## Error codes

All errors follow:

```json
{ "status": "FAILED", "errorCode": "<code>", "serviceId": ..., "transId": ..., "timestamp": ... }
```

| Code | When we return it |
|---|---|
| `10001` | Missing or invalid `Authorization` header (HTTP 400 per Uzum spec — the body is what matters; status code mirrors all other failures) |
| `10002` | Body is not valid JSON |
| `10005` | Required field missing (`userId`, `planId`, `transId`, `amount`, etc.) |
| `10006` | `serviceId` does not match `UZUM_SERVICE_ID` |
| `10007` | Unknown plan, unknown user, unknown transId, or amount mismatch |
| `10008` | Replay with same `transId` but different details, **or** user already has an active subscription/daily-pass for this plan |
| `10009` | `/confirm` called on a transaction that's already been `REVERSED` |
| `99999` | Unexpected server error |

## How the Uzum team can test (Postman)

Once we deploy and share credentials, the Uzum team can run the following flow:

1. `POST /api/v2/transaction/uzum/check` with a real `userId` + a valid `planId`.
   Expect `200 OK` with `status: "OK"`.
2. `POST /api/v2/transaction/uzum/create` with a fresh `transId` (UUID) and the
   matching `amount` (plan price × 100). Expect `200 OK` with `status: "CREATED"`.
3. `POST /api/v2/transaction/uzum/confirm` with the same `transId`. Expect
   `200 OK` with `status: "CONFIRMED"`. The user's subscription is granted at
   this point.
4. (Optional) `POST /api/v2/transaction/uzum/status` to read state back.
5. (Optional) `POST /api/v2/transaction/uzum/reverse` to cancel. Note: if the
   transaction was already `CONFIRMED`, we mark it `REVERSED` but do **not**
   auto-revoke the granted subscription — that's logged for manual review.

Negative tests worth running:

- Bad `Authorization` header → `400` with errorCode `10001`.
- Wrong `serviceId` → `400` with errorCode `10006`.
- `planId` not in the catalog → `400` with errorCode `10007`.
- `amount` not matching plan price → `400` with errorCode `10007`.
- Replay `/create` with same `transId` and same params → `200` (idempotent).
- Replay `/confirm` after success → `200` with same body (idempotent).
- Call `/confirm` on a reversed transaction → `400` with errorCode `10009`.

## Outstanding items (need confirmation from Uzum team)

1. Final `serviceId`(s) for production once Uzum issues them.
2. Final `username` / `password` for production.
3. Confirm the field name the customer's input arrives under in `params`. We currently
   accept `userId` / `user_id` / `account` for the user id and `planId` / `plan_id` /
   `tariff` for the plan — whichever Uzum's catalog uses will work without code
   changes.
4. Deeplink / QR support — Uzum confirmed (2026-06-15) that deeplinks exist and can
   be tested on production. Exact URL format / parameters still to be shared by
   Uzum before we wire up the frontend "Pay with Uzum" button.
