# Account Deletion — Design

**Date:** 2026-07-13
**Status:** Approved, pending implementation
**Supersedes:** the in-place soft-delete / `archived` flag implementation currently on `feat/user-archive`

## Problem

Google Play requires a "delete my account" URL. The first implementation soft-deleted in place: a
`archived: true` flag on the user and every row they owned, plus read filters and a runtime guard to
keep flagged rows invisible.

That design fails one requirement it was never asked about: **archived rows stay in the live
collections, so the external analytics ETL keeps counting deleted users in its statistics.** The ETL
is zero-touch — we cannot change its queries, nor which collection names it reads.

Product also revised the policy. Deletion is no longer a permanent per-identity ban. A person who
deletes their account and logs back in with the same Google/Telegram identity gets a **fresh, empty
account**. Blocking is a separate, admin-controlled concern.

## Decisions

| Question | Decision |
|---|---|
| Where does deleted data go? | **One summary document** in a new `archived_users` collection. Owned rows are hard-deleted. |
| Is the archive restorable? | **No.** It is anonymized analytics history, not a backup. Deletion is irreversible. |
| Can a deleted user return? | **Yes.** Fresh account, zero carryover — no history, no credits, no subscription. |
| Can a blocked user delete? | **No** — `403`, "contact support". A super-admin can delete on their behalf. |
| Do we need a blocklist that survives deletion? | **No.** See "Why there is no blocklist" below. |

## Data model

One new collection. Not thirteen.

```
archived_users
  _id                 ObjectId          # its own id, NOT the user's
  original_user_id    "<google sub | telegram id>"   # indexed, NOT unique
  status              "in_progress" | "complete"

  # lifecycle
  archived_at, deletion_reason
  created_at, web_client, referral_source

  # usage volume (counts only — no content, titles, or filenames)
  totals { sessions, messages, files, tokens, credits_spent }

  # monetization
  plan, ever_paid, revenue_total, subscription_state_at_deletion
```

No name, no email, no phone, no picture, no message content.

`original_user_id` is **not unique**: one document per *deletion event*. Delete → re-signup → delete
again produces two rows, not a duplicate-key crash.

It is stored **raw, not hashed**. If the ETL turns out to be incremental/append-only, this collection
is the feed that tells the warehouse which `user_id`s to purge. Hashing it would make that impossible.

## The delete flow

Production Mongo is standalone (`.env` connection string has no `replicaSet`), so **multi-document
transactions are unavailable**. Every step is therefore idempotent, and the `archived_users` document
doubles as the progress record. No flags on live rows; no read filtering anywhere.

1. Load the `users` doc.
   - Missing, and a `complete` archive doc exists → `200 already_deleted`.
   - Missing, no archive doc → `404`.
2. An `in_progress` archive doc exists for this user (a prior run crashed) → **resume from step 5,
   reusing its totals.** Never recompute totals against half-deleted collections. This check runs
   *before* the block check on purpose: the deletion was authorized when it started, and a block
   applied afterwards must not strand the account half-deleted.
3. `is_blocked` and the caller is not a super-admin → `403 "Your account is blocked. Contact support."`
4. Compute totals → insert the `archived_users` doc with `status: "in_progress"`.
5. Re-key retained payment/tax rows (see below).
6. `delete_many({<owner field>: {$in: owner_values}})` across the 12 owned collections.
7. `delete_one` the `users` doc — **last**.
8. Flip the archive doc to `status: "complete"`.

Steps 5–8 are no-ops on re-run, so a crash at any point is repaired by the next call.

### Owned collections — hard-deleted (12)

Taken from `AccountArchiveService._owned_collections`, minus `fingerprints`:

| Collection | Owner field |
|---|---|
| `sessions`, `messages`, `files` | `user_id` |
| `creditusage`, `subscriptions`, `daily_subscriptions` | `user_id` |
| `user-promos`, `token_counts`, `telegram_chats` | `user_id` |
| `projects` | `owner_id` |
| `project_members` | `user_id` |
| `project_invites` | `created_by` |

`telegram_chats` stores `user_id` as an int — keep the existing `_owner_values` str+int matching.

### `fingerprints` — deliberately NOT deleted

It exists to detect multi-account abuse. Wiping a user's device history on deletion hands abusers a
one-click "reset my fingerprint" button. The rows stay.

### Payment / tax — retained but re-keyed

These are kept for the 1-year retention obligation, so they are **not** deleted. But Google `sub`
*is* the Mongo `_id`, so a returning user's fresh account gets the **same `_id`** and would otherwise
inherit the previous incarnation's billing history — and possibly a live subscription.

At step 5, re-key them away from the reusable id:

```
update_many({"user_id": <sub>}, {"$set": {"user_id": "deleted:<sub>:<ts>"}})
```

Targets (each confirmed to carry `user_id`):

- `payment_invoices` — `services/payments/base.py:455`, indexed at `:118`
- `transactions` (payme) — `services/payments/payme.py:16`
- `click_transactions` — `services/payments/click.py:258`
- `uzum_transactions` — `services/payments/uzum.py:61`
- `payme_fiscal` — audit during implementation; re-key only if it carries `user_id`

This is why we do **not** need the larger auth refactor of moving identity off `_id` into a
`google_sub` field. That would touch every `user_id` foreign key in the system. Re-keying four
payment collections is ten lines.

## Re-login

`users` has no document → the existing `create_user` upsert creates a fresh account with `_id = sub`.
**No auth code is needed for this; it already works.**

The fresh account is genuinely clean: owned rows are gone, and payment rows were re-keyed away from
`sub` in step 5.

## Why there is no blocklist

An earlier draft added a `blocked_identities` collection so a ban could outlive the account it
belonged to. Making blocked users undeletable removes the need entirely:

- A blocked user cannot delete → their `users` doc (carrying `is_blocked: true`) never leaves.
- They log back in → `auth.py` finds the doc → still blocked.

Nothing needs to survive deletion, because a blocked account cannot be deleted. Ban evasion is closed
by construction.

**Residual risk, accepted:** a user who misbehaves and deletes their account *before* an admin blocks
them can return with a clean slate. That is inherent to the "fresh start" policy. `fingerprints` still
catches the device-level pattern.

## Blocked users and Play policy

Google Play requires that users can *request* account deletion. Returning `403` to blocked users means
they cannot do so in-app. That is acceptable only because an alternate path exists:

**`POST /users/{user_id}/delete-account` called with the super-admin key skips the `is_blocked` check.**

Support receives the request by email and an admin runs it. Same gating pattern already used by
`block_user` / `unblock_user` (`api/v2/history/users.py:229`, `:246`).

The super-admin key must be detected *optionally* — presence, not requirement — so a normal user call
still succeeds. Follow the header-reading pattern in `verify_api_key_or_dt_key`
(`security/dependencies.py:115`); a present-but-wrong super-admin key should still `403`.

## Code to remove

The entire soft-delete apparatus exists to make flagged rows invisible. With the rows gone, none of it
is needed:

- `_ACTIVE_ONLY` and its ~14 call sites in `services/chat_history_service.py`
- `verify_not_archived` / `assert_not_archived`, plus wiring in `main.py`, `api/v2/history/files.py`,
  `api/v2/history/projects.py`, `security/__init__.py`
- the `archived` checks in `api/v2/auth.py:246` and `:326`
- `ChatHistoryService.is_user_archived`
- the partial unique phone index and `scripts/add_active_phone_unique_index.py` (a plain unique index
  works again)
- `tests/test_archive_guard.py`

This also kills the entire "did someone remember the filter" bug class — including the two live gaps
this design review found: the missing filter in `get_files_by_project`, and the archived-user leak in
`fingerprint_service.list_multi_account` / `_count_users_for_visitor`.

## Testing

- **Deletion:** totals are computed correctly; all 12 owned collections are emptied for the user;
  `fingerprints` survives; payment rows are re-keyed, not deleted.
- **Idempotency / crash recovery:** a run interrupted after the archive doc is written resumes and
  reuses the original totals rather than recomputing against half-deleted data.
- **Blocked:** a blocked user gets `403`; the same call with the super-admin key succeeds.
- **Re-login:** after deletion, signing in with the same Google `sub` yields a fresh account with no
  sessions, no credits, no subscription, and no visibility of the prior incarnation's payment history.
- **Already deleted:** repeat call returns `200 already_deleted`, not `404` and not a second archive row.

## Open questions

1. **Is the ETL full-reload or incremental?** Unresolved, and it is the one thing this design cannot
   fix. Hard-deleting from Mongo cleans Mongo. If the ETL is incremental, everything it already
   exported is still in the warehouse and always will be — analytics needs a deletion pass on their
   side, driven by the `archived_users` feed.
2. **Exact summary field list.** Lifecycle + usage + monetization is agreed; the precise field names
   should be confirmed against what analytics actually consumes.
3. **`payme_fiscal`** — does it carry `user_id`? Audit during implementation.

## Known limits (accepted, not solving now)

- A run that crashes between steps 4 and 7 leaves the user **able to log in** until someone retries.
  The window is sub-second. A sweeper for stuck `in_progress` documents is YAGNI until it bites.
- Deletion is irreversible. There is no restore path, by design — an anonymized archive cannot be
  un-anonymized.
