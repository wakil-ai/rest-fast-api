# Frontend Handoff — File Upload Restricted to Paid Plans

> Backend changes for the "File uploading limitation for free users" task are
> **done and merged into the API behavior**. This document describes exactly what
> the frontend needs to do to integrate. No backend action is required from the
> frontend team — the backend is the source of truth and already enforces the rule.

---

## 1. What changed on the backend

File upload is now a **paid-plan-only** feature. Both upload endpoints reject
ineligible users **before** reading or processing the file:

| Endpoint | Purpose |
| --- | --- |
| `POST /api/v2/history/files` (multipart, `file` + `user_id`) | Message file upload |
| `POST /api/v2/history/projects/{project_id}/files` (multipart, `file` + `user_id` …) | Project file upload |

> All these endpoints already require the standard `x-api-key` (or DT key) header,
> as before — that is unchanged.

A user is **eligible to upload** if **any** of the following is true:

- They have an **active subscription** (`end_ms` in the future and `daily_credits > 0`).
- They have an **active daily pass**.
- They have an **unlimited promo code**.

Everyone else (free tier, expired subscription, finite/limited promo) is **denied**.

---

## 2. Denial response contract

When an ineligible user attempts an upload, the endpoint returns:

```
HTTP 402 Payment Required
Content-Type: application/json

{
  "detail": {
    "code": "FILE_UPLOAD_REQUIRES_PAID_PLAN",
    "message": "File upload is available for paid plans only.",
    "upgrade_required": true
  }
}
```

> ⚠️ **Note the shape.** Because the backend uses FastAPI's standard error
> envelope, the contract is nested under `detail`. Read it as
> `response.data.detail.code` (axios) / `(await res.json()).detail.code` (fetch).

**Always branch on `detail.code === "FILE_UPLOAD_REQUIRES_PAID_PLAN"`**, not on the
human-readable `message` (the message text may be localized/changed later). When
`detail.upgrade_required === true`, route the user into the upgrade flow.

The status code is **402 Payment Required**.

---

## 3. Deriving upload eligibility ahead of time (UI gating)

Use the existing subscription status endpoint to gate the UI **before** the user
attempts an upload — no new backend endpoint was added:

```
GET /api/v2/transaction/payme/subscriptions/{user_id}
```

Returns `UserSubscriptionResponse`. Relevant fields:

```jsonc
{
  "user_id": "…",
  "active": true,              // subscription active
  "tier": "pro",
  "daily_credits": 200,
  "end_ms": 1750000000000,
  "daily_pass_active": false,  // daily pass active
  "daily_pass_daily_credits": 0,
  "effective_daily_credit_limit": 200,
  "today_remaining_credits": 180
  // …
}
```

Derive the client-side boolean:

```ts
function canUploadFiles(sub: UserSubscriptionResponse): boolean {
  // Mirrors the backend rule. Note: unlimited-promo users are NOT reflected
  // in this payload, so treat the 402 response as the ultimate source of truth.
  return Boolean(sub.active) || Boolean(sub.daily_pass_active);
}
```

> The subscription payload does **not** expose promo status, so a small number of
> unlimited-promo users may be gated off in the UI yet still be allowed by the
> backend. That's fine — the backend allows them; the UI just won't pre-enable the
> control. The **402 contract in §2 remains the authoritative check.** Never treat
> the client-derived boolean as final.

---

## 4. Frontend tasks (from the task spec)

1. **Plan status detection** — call `GET /transaction/payme/subscriptions/{user_id}`
   (`GET /api/v2/transaction/payme/subscriptions/{user_id}`) on load / auth,
   compute `canUploadFiles` (see §3), keep it in app state.
2. **UI gating** — when `canUploadFiles` is false:
   - Disable or hide the message-composer attach button and the project
     "Upload document" control.
   - Show a lock icon + tooltip/CTA: **"Upgrade to upload files."**
   - Clicking the locked control opens the upgrade modal/route.
3. **Error fallback** (handles stale state / race where gating is bypassed):
   - On any upload request, if the response is `402` **and**
     `detail.code === "FILE_UPLOAD_REQUIRES_PAID_PLAN"`, show the upgrade
     modal/toast — **do not** surface a generic "upload failed" error.
   - Refresh local subscription state so the UI re-gates immediately.
4. **Frontend tests**
   - Free user → upload controls gated (hidden/disabled, CTA visible).
   - Paid user (active sub OR active daily pass) → upload controls enabled.
   - Mock a `402 FILE_UPLOAD_REQUIRES_PAID_PLAN` response → upgrade UX shown,
     no generic error.

---

## 5. Suggested error-handling snippet

```ts
async function uploadMessageFile(form: FormData) {
  const res = await fetch("/api/v2/history/files", { method: "POST", body: form });

  if (res.status === 402) {
    const body = await res.json().catch(() => null);
    if (body?.detail?.code === "FILE_UPLOAD_REQUIRES_PAID_PLAN") {
      openUpgradeModal({ reason: "file_upload" });
      refreshSubscriptionState();
      return; // handled — do not fall through to generic error
    }
  }

  if (!res.ok) throw new Error("Upload failed"); // genuine failures only
  return res.json();
}
```

---

## 6. QA scenarios to verify (frontend side)

1. Free user tries message upload → blocked + upgrade prompt.
2. Free user tries project upload → blocked + upgrade prompt.
3. Paid user uploads the same files successfully.
4. Direct API call (Postman/cURL) as a free user → `402` (confirms backend gate;
   UI not involved).
5. Existing chat flow **without** file upload remains unaffected.

---

## 7. Backend references

- Entitlement rule: `app/services/rate_limit_service.py` → `RateLimitService.can_upload_files`
- Guard + error contract: `app/utils/entitlements.py` → `ensure_can_upload_files`
- Enforced in: `app/api/v2/history/files.py`, `app/api/v2/history/projects.py`
- Error docs: `docs/reference-error-codes.md`
- Tests: `tests/test_upload_entitlement.py`
