# Admin subscription management

Operator surface for fixing subscriptions by hand — a user who paid through an
external account (so no provider webhook fired), a goodwill top-up, a refund.

**This replaces editing MongoDB directly.** Compass edits look correct and silently
do nothing, for reasons explained under [Why the document lies](#why-the-document-lies).

- UI: `admin.wakil.ai` → **Subscriptions**
- API: `/api/v2/admin/subscriptions`, gated by `x-super-admin-key`

## Why the document lies

The `subscriptions` document is not the source of truth for what a user can spend.
`RateLimitService._get_pool_credits_remaining` computes the effective balance as:

```
total_credits - max(creditusage_used_over_window, total_credits - credits_remaining)
```

Two consequences that have burned us:

**`total_credits` is a hard ceiling.** Setting `credits_remaining: 3000` on a
document with `total_credits: 6000` and 4380 credits already in the `creditusage`
ledger leaves the user with 1620, not 3000. The API must set `total_credits` to
`ledger_usage + desired_balance`; every mutation here does that automatically.

**The window is day-granular.** `creditusage` is matched on a `YYYY-MM-DD` string
between `start_ms` and `end_ms`, so the window includes the whole start and end
days — and any credits the user spent on free or daily-pass days that fall inside
it. Extending `end_ms` over a gap therefore retro-debits the pool. `preserve_remaining`
(on by default) compensates.

A third trap is types: a `end_ms` stored as a String renders fine over HTTP but never
matches the Mongo-side `{"end_ms": {"$gt": now_ms}}` predicate in
`try_consume_pool_credits`, so the user can spend nothing while the API reports them
active. All writes here coerce to `int`, and the diagnostic flags existing damage.

## Diagnose first

```
GET /api/v2/admin/subscriptions/{user_id}
```

Returns four blocks side by side — this is the call that answers "the subscription
looks fine but the app says free":

| Block | What it tells you |
|---|---|
| `raw` | the stored document untouched, plus a BSON type per field, and every daily lot |
| `computed` | exactly what the client receives from `GET /transaction/payme/subscriptions/{id}` |
| `credit_usage` | the window dates, the total, and per-day ledger rows |
| `reconciliation` | stored vs. effective, and the `drift` between them |
| `warnings` | typed problems: `END_MS_NOT_INT`, `ENTITLEMENT_DRIFT`, `WINDOW_STARTS_IN_FUTURE`, `LEGACY_SUBSCRIPTION_ON_USER_DOC`, `MULTIPLE_ACTIVE_DAILY_LOTS`, … |

`drift != 0` means the document is misreporting the balance. In the UI this renders
as a red "stored ≠ effective" banner.

If `user.exists` is `false`, stop: the id is wrong, and granting against it produces
entitlement nobody can see. Telegram accounts key on the bare numeric id and often
carry `phone_number: null`, so a phone search finding nothing proves nothing.

## Mutating

All four mutations require three headers and a `reason`:

| Header | Meaning |
|---|---|
| `x-super-admin-key` | authorization |
| `x-admin-operator` | who is doing it — a **claim**, recorded next to a fingerprint of the key actually used |
| `x-admin-request-id` | UUID idempotency key; replaying one returns `409 ADMIN_ACTION_DUPLICATE` instead of applying twice |

### Grant

```
POST /api/v2/admin/subscriptions/{user_id}/grant
{"tier": "standard", "period": "monthly", "reason": "paid 300k externally, receipt 4471",
 "start_mode": "after_current", "override_eligibility": false}
```

- `start_mode: "after_current"` stacks behind an active window and **preserves the
  carried balance** (plain `upsert_subscription` loses it).
- `start_mode: "now"` closes the current window and starts fresh.
- A user with an active standard/pro subscription trips `409 ACTIVE_SUBSCRIPTION_EXISTS`.
  That guard is right for purchases; set `override_eligibility: true` to bypass it
  deliberately. The bypassed code lands in the audit entry.
- Daily passes (`period: "daily"`) become stacking lots in `daily_subscriptions`,
  never a row in `subscriptions`.

### Extend

```
POST /api/v2/admin/subscriptions/{user_id}/extend
{"extend_days": 30, "preserve_remaining": true, "reason": "external renewal"}
```

Moves `end_ms` only. Provide exactly one of `extend_days` or `new_end_ms`.

### Adjust credits

```
POST /api/v2/admin/subscriptions/{user_id}/adjust-credits
{"mode": "set", "credits": 3000, "reason": "support credit"}
```

`mode: "set"` targets an absolute effective balance; `mode: "delta"` adds or
subtracts. The endpoint computes `total_credits` from the ledger, then re-reads and
verifies. If a spend lands mid-flight it retries once, then returns
`409 ADMIN_RECONCILE_FAILED` with both numbers rather than leaving a wrong balance.

Requires an active subscription — grant or extend first.

### Revoke

```
POST /api/v2/admin/subscriptions/{user_id}/revoke
{"target": "pool", "zero_credits": true, "reason": "refunded"}
```

Sets `end_ms` into the past and stamps `revoked_by`/`revoke_reason`. **Never deletes.**
`target` may be `pool`, `daily_lot` (with `daily_lot_id`), or `all`.

**Revoke then grant is the clean reset** for a tangled subscription: `upsert_subscription`
computes `start_ms = max(now_ms, existing_end_ms)`, so an expired window makes the new
grant start now with no carry-over.

## Audit

Every attempt — including failures — writes to `admin_audit_logs` with the operator,
reason, params, and before/after snapshots. Nothing in the API updates or deletes
those rows.

```
GET /api/v2/admin/subscriptions/audit?user_id=…&operator=…&action=…&limit=50
```

Because these grants have no invoice behind them, this collection is the only record
for revenue reconciliation. Export it when closing the books.

## Known limitations

- **The operator name is self-reported.** Anyone holding the super-admin key can write
  any name. The entry also stores a fingerprint of the key used, the source IP, and the
  user agent; issuing per-operator keys is the upgrade path that turns the claim into proof.
- **The panel uses one shared login** with a plaintext credential comparison and
  `secure: false` on the cookie. That cookie now gates money-equivalent actions.
- No refunds, invoices, or transaction records are created — these grants are
  entitlement-only.
- The `creditusage` ledger is never edited; `total_credits` is the only lever.
- No backdated grants, no bulk operations.
