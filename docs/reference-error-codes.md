# Error Codes Reference

All custom exceptions and HTTP error responses the WakilAI API can return.

**Source:** [src/core/exceptions.py](../src/core/exceptions.py),
[src/core/error_codes.py](../src/core/error_codes.py),
[src/core/error_handlers.py](../src/core/error_handlers.py)

---

## Wire contract

Every error response — from a coded exception, an uncoded `HTTPException`, a
422 validation failure, or an unhandled 500 — is normalized by a global
handler into one envelope:

```json
{
  "detail": "Insufficient credits. You have 0/30 credits remaining.",
  "error": {
    "code": "CREDITS_EXHAUSTED",
    "message": "Daily credit limit reached.",
    "params": { "remaining": 0, "limit": 30, "required": 1 }
  }
}
```

- **`detail` is always a plain string.** Both mobile clients decode it as
  such; this is never changed, even for the legacy dict-`detail` codes below.
- **`error.code`** is the stable, permanent machine-readable code. Once
  shipped, a code is never renamed or removed — clients may already switch
  on it. Prefer this over parsing `detail` text.
- **`error.message`** is an English, developer-facing fallback. Product
  surfaces should localize by `error.code`, not display this string.
- **`error.params`** carries the structured values (counts, IDs, seat
  limits, retry-after seconds, …) a client needs to render specific copy.

Legacy exception sites that predate this contract (`FILE_UPLOAD_REQUIRES_PAID_PLAN`,
`ACTIVE_SUBSCRIPTION_EXISTS`, `ACTIVE_DAILY_PASS_SAME_TIER`,
`DAILY_PASS_DOWNGRADE_NOT_ALLOWED`) used to put the whole `{code, message,
...}` object directly in `detail`. The handler now unwraps them into the
same envelope, so `detail` is a string there too.

Full code catalog — status, English fallback message, and expected `params`
keys — lives in [`ERROR_CATALOG`](../src/core/error_codes.py). The
per-exception sections below are kept for the narrative "when does this
happen / what should I do" context; the code table there is the source of
truth for the wire shape.

---

## Chat Errors (4xx / 5xx)

### `QueryTooLongException`

```
HTTP 400 Bad Request
{
  "detail": "Query too long. Maximum 5000 characters allowed, got 6321 characters."
}
```

The query text exceeds `MAX_QUERY_LENGTH` (default: 5000 characters). Trim the user's input before sending.

---

### `InsufficientCreditsException`

```
HTTP 429 Too Many Requests
{
  "detail": "Insufficient credits. You have 5/100 credits remaining. This request requires 10 credit(s)."
}
```

The user's daily credit pool is exhausted for the requested assistant type. Credits reset at midnight UTC. See [Explanation: Credit System](explanation-credit-system.md) for credit costs per assistant.

---

### `FILE_UPLOAD_REQUIRES_PAID_PLAN`

```
HTTP 402 Payment Required
{
  "detail": "File upload is available for paid plans only.",
  "error": {
    "code": "FILE_UPLOAD_REQUIRES_PAID_PLAN",
    "message": "File upload is available for paid plans only.",
    "params": { "upgrade_required": true }
  }
}
```

File upload is a paid-plan feature. Returned by the upload endpoints
(`POST /api/v2/history/files` and `POST /api/v2/history/projects/{project_id}/files`) when the
user is **not** entitled to upload. A user is entitled if they have an active
subscription, an active daily pass, or an unlimited promo code.

The check runs at the API layer **before** any file is read or processed, and the
backend is the source of truth — it cannot be bypassed from the client. Clients
should read the machine-readable `error.code` (not the human message) and route
the user to the upgrade flow. See [Explanation: Credit System](explanation-credit-system.md)
and the subscription status endpoint `GET /api/v2/transaction/payme/subscriptions/{user_id}`
(`active` flag) for deriving upload eligibility ahead of time.

> Historical note: before this contract existed, `detail` itself was the
> `{code, message, upgrade_required}` object. It is now always a string —
> see [Wire contract](#wire-contract) above.

**Source:** [src/utils/entitlements.py](../src/utils/entitlements.py)

---

### `ChatGenerationException`

```
HTTP 500 Internal Server Error
{
  "detail": "Failed to generate answer. Please try again later."
}
```

The LLM generation step failed — network error, upstream API timeout, or model refusal. Retry with exponential backoff. Check the `rest-api-llm` service logs/traces for the specific failure point.

---

### `FlowExecutionException`

```
HTTP 500 Internal Server Error
{
  "detail": "Failed to execute RAG flow. Please try again later."
}
```

The internal LLM service returned an unrecoverable chat error or could not be reached. Logs contain the full traceback. Common causes: `LLM_SERVICE_URL` is wrong, the internal token is missing, or `rest-api-llm` is unavailable.

---

### `AssistantConfigException`

```
HTTP 400 Bad Request
{
  "detail": "Invalid assistant configuration: unknown_assistant"
}
```

The `assistant` field in the request contains an unrecognized value. Valid values: `main`, `tax`, `court`, `contract_analyzer`, `criminal_court`, and legacy aliases (`soliq`, `shartnoma`, `mamuriy_sud`, `deepresearch`).

---

### `StreamingException`

```
HTTP 500 Internal Server Error
{
  "detail": "Streaming response failed. Please try again."
}
```

An error occurred while relaying the SSE stream from `rest-api-llm`. Retry the request and check the internal service logs.

---

### `TooLongFileContentException`

```
HTTP 400 Bad Request
{
  "detail": "Content of the file 'contract.pdf' is too long. Maximum 50000 characters allowed, got 87000 characters."
}
```

Extracted text from an uploaded file exceeds `FILE_CONTENT_TOKEN_LIMIT` (default: 50,000 characters). Split the file into smaller documents before uploading.

---

### `QueryValidationException`

```
HTTP 400 Bad Request
{
  "detail": "<validation message>"
}
```

Base class for all query validation failures. Includes `QueryTooLongException`.

---

## History Service Errors

### `UserNotFoundError`

```
HTTP 404 Not Found
{
  "detail": "User with ID 'user_abc' not found."
}
```

The `user_id` does not exist in the `users` collection. Create the user first via `POST /api/v2/history/users`.

---

### `SessionNotFoundError`

```
HTTP 404 Not Found
{
  "detail": "Session with ID 'sess_xyz' not found."
}
```

The `session_id` does not exist or belongs to a different user. Create a session via `POST /api/v2/history/sessions`.

---

### `MessageNotFoundError`

```
HTTP 404 Not Found
{
  "detail": "Message with ID 'msg_123' not found."
}
```

---

### `UserBlockedError`

```
HTTP 403 Forbidden
{
  "detail": "User with ID 'user_abc' is blocked and cannot perform this action."
}
```

The user has been blocked by an admin via `PATCH /api/v2/history/users/{user_id}`. Unblock through the super-admin endpoint.

---

### `UserAlreadyExistsException`

```
HTTP 400 Bad Request
{
  "detail": "User with external ID 'google_12345' already exists."
}
```

Returned by auth endpoints when attempting to create a duplicate user. The existing user record should be retrieved instead.

---

### `InvalidInputError`

```
HTTP 400 Bad Request
{
  "detail": "<validation message>"
}
```

Generic input validation failure in history operations (missing required fields, invalid formats, etc.).

---

## Standard FastAPI Errors (not custom)

| Status | When it occurs |
|--------|---------------|
| `401 Unauthorized` | Missing or invalid `x-api-key` (or configured `API_KEY_NAME`) header |
| `403 Forbidden` | Valid API key but insufficient permissions (e.g. super-admin endpoint with regular key) |
| `422 Unprocessable Entity` | Pydantic validation failure — request body fields missing or wrong type |
| `404 Not Found` | Route does not exist (check prefix: `/api/v3/` vs `/api/v2/`) |

---

## Auth Errors (Payme)

Payme callbacks use JSON-RPC and return errors in the RPC body rather than HTTP status codes. HTTP 200 is returned with a structured error:

```json
{
  "jsonrpc": "2.0",
  "id": 1,
  "error": {
    "code": -31300,
    "message": "AUTHORIZATION_FAILURE"
  }
}
```

Payme RPC error codes follow the Payme documentation.

---

## Admin Subscription Errors

Raised by `/api/v2/admin/subscriptions` (see
[Admin Subscription Management](admin-subscription-management.md)). All use the
standard coded envelope: `{"detail": "...", "error": {"code": ..., "params": {...}}}`.

| Code | Status | Meaning |
|---|---|---|
| `ADMIN_OPERATOR_REQUIRED` | 400 | `x-admin-operator` missing or not 2–64 chars of `[A-Za-z0-9._@ -]` |
| `ADMIN_REQUEST_ID_REQUIRED` | 400 | `x-admin-request-id` missing or not a UUID |
| `INVALID_SUBSCRIPTION_PLAN` | 400 | tier/period pair is not in the catalog (note: `test` exists only when `DEBUG`) |
| `EXTEND_TARGET_INVALID` | 400 | neither or both of `extend_days` / `new_end_ms` supplied |
| `SUBSCRIPTION_NOT_FOUND` | 404 | no row in `subscriptions` for this user |
| `DAILY_LOT_NOT_FOUND` | 404 | no daily lot with that `daily_lot_id` |
| `SUBSCRIPTION_NOT_ACTIVE` | 409 | window already closed — grant or extend before adjusting credits |
| `ADMIN_ACTION_DUPLICATE` | 409 | this `x-admin-request-id` was already used; `params.audit_id` names the original |
| `ADMIN_SUBSCRIPTION_CONFLICT` | 409 | another writer moved the document mid-flight; re-read and retry |
| `ADMIN_RECONCILE_FAILED` | 409 | the effective balance could not be made to match; `params` carries desired vs. actual |

`ACTIVE_SUBSCRIPTION_EXISTS` (409) also surfaces here from the shared eligibility
check. On the admin grant route it is bypassable with `override_eligibility: true`.

---

## Debugging Errors

1. **Check `rest-api-llm` traces** — LLM-call, retrieval, and timing observability lives in the `rest-api-llm` service.
2. **Check Loguru output** — errors from `chat_service.py` include full tracebacks when logged with `exc_info=True`.
3. **Health check** — `GET /health` returns 200 when the API is up (no auth required). If it's down, the process crashed or the port is wrong.
4. **Swagger UI** — `GET /docs` (HTTP Basic Auth) allows manual request testing with all schemas visible.

---

## Related

- [Explanation: Credit System](explanation-credit-system.md)
- [Admin Subscription Management](admin-subscription-management.md)
- [How-To: Monitor Usage](howto-monitor-usage.md)
- [Onboarding Guide: Debugging Tips](onboarding.md#debugging-tips)
