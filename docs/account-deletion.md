# Account deletion (soft-delete / archive)

Backs the Google Play mandatory "delete my account" flow. Deleting an account is a
**soft-delete**: the user and all data they own are marked `archived` in place and
become invisible/unusable across the product. Financial and tax records are retained
for a legally required period, then purged by a separate job.

## Endpoint

```
POST /api/v2/history/users/{user_id}/delete-account
```

- **Auth:** same `x-api-key` header as every other call. `user_id` is taken from the
  path (consistent with the other user routes).
- **Body (optional):** `{ "reason": "..." }` — free text, ≤ 500 chars, stored as audit
  metadata.
- **Idempotent:** calling it again on an already-archived account returns `200` with
  `already_archived: true` (not an error).

### Response `200`

```json
{
  "user_id": "1078xxxxxxxxxxxxx",
  "status": "archived",
  "already_archived": false,
  "archived_at": "2026-07-02T09:15:00Z",
  "message": "Account archived successfully"
}
```

| Field | Meaning |
| --- | --- |
| `already_archived` | `false` on the first successful call, `true` on any repeat |
| `archived_at` | When the account was archived (UTC); the original timestamp on repeats |

### Errors

| Status | When |
| --- | --- |
| `404` | No user exists with that `user_id` |
| `400` | Missing/blank `user_id` |

## What happens on deletion

**Archived immediately** (marked `archived: true` in place, then hidden from all
reads): the user profile plus sessions, messages, files, projects, project
memberships/invites, credit usage, subscriptions & daily passes, promo assignments,
token counts, Telegram chat links, and device fingerprints.

**Retained, then purged after 1 year:** payment and tax records (transactions,
provider invoices). These are kept to meet financial/tax obligations and are removed by
a separate retention job once the period elapses.

## Consequences

- The archived account can **no longer log in or sign up** with the same identity
  (Google / Telegram) — deletion is permanent for that login identity.
- Any request made on behalf of an archived account is rejected with `403`.

---

## Public "Delete account" page copy

> Use the text below (translated as needed) on the public account-deletion page linked
> from the app store listing.

**Delete your WakilAI account**

You can request deletion of your WakilAI account and associated data at any time, from
inside the app: open **Settings → Account → Delete account**, and confirm.

When you delete your account:

- Your profile and your content — chats, uploaded files, projects, and related
  activity — are removed from the app immediately and can no longer be accessed.
- Your account is permanently closed. You will not be able to sign back in, and signing
  up again with the same Google or Telegram login will not restore any previous data.
- Payment and billing records are kept for up to **1 year** to meet legal and tax
  requirements, and are then permanently deleted.

If you cannot access the app, email **support@wakil.ai** from the address associated
with your account and we will process the request on your behalf.
