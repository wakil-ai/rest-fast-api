# Phone Number Deduplication

The `scripts/dedupe_phone_numbers.py` migration sanitizes the `users` collection
so that every phone number belongs to exactly one user, then enforces that rule
at the database level with a unique index.

It is safe to run repeatedly: once duplicates are cleared and the index exists,
re-running is a no-op.

## What It Does

1. **Finds duplicates.** Groups users by `phone_number` (ignoring `null` and
   empty strings) and selects groups shared by more than one user.
2. **Keeps one winner per number.** All other records in the group have their
   `phone_number` set to `null`, so those users are prompted to enter a new
   number on their next login. No user documents are deleted.
3. **Backs up before writing.** Cleared records (`_id`, `user_id`, old
   `phone_number`) are dumped to `scripts/backups/phone_dedupe_<timestamp>.json`
   for rollback.
4. **Invalidates caches.** Best-effort deletion of the `user:{id}` Redis keys so
   a stale phone number does not survive the cache TTL.
5. **Enforces uniqueness.** Creates a UNIQUE partial index `uniq_phone_number`
   on `phone_number` (string values only) so future duplicates are rejected by
   MongoDB. The index is partial so the many `null` documents do not collide.

## Winner Selection

When a number is shared by several users, the record kept is chosen
deterministically by, in order of priority:

1. **Activity score** (count of `sessions` + `messages` + `files` +
   `subscriptions` for that user) — highest wins.
2. **`updated_at`** — most recent wins.
3. **`created_at`** — most recent wins.
4. **`_id`** — lowest wins (final tiebreak).

Activity is weighed first so a real, used account is never dropped in favour of
an empty duplicate. Use `--no-activity-score` to rank purely by recency.

## Usage

Dry run first — it reports the duplicate groups and which record would be kept
or cleared without writing anything:

```bash
.venv/bin/python scripts/dedupe_phone_numbers.py
```

Apply the cleanup and build the unique index:

```bash
.venv/bin/python scripts/dedupe_phone_numbers.py --apply
```

## Flags

| Flag | Description |
| --- | --- |
| `--apply` | Write changes. Without it the script only reports (dry run). |
| `--index-only` | Skip cleanup and only create the unique index. Useful once data is already clean. |
| `--no-index` | Run the cleanup but skip creating the unique index. |
| `--no-activity-score` | Rank winners by recency only, ignoring related-record activity. |
| `--no-cache-invalidation` | Do not invalidate Redis `user:{id}` caches after clearing numbers. |

Notes:

- `--apply` is required for any database write; every other flag only changes
  *what* runs, not *whether* it writes.
- Without `--apply`, `--index-only` reports the index it *would* create.
- The index build refuses to run while any duplicate remains, so a partial or
  interrupted cleanup cannot produce an invalid index.

## Recommended Procedure

1. Run a **dry run** against the target database and review the report.
2. Run with **`--apply`** during a low-traffic window.
3. Confirm the success output: `All duplicates resolved.` and the index
   creation line.
4. Keep the generated `scripts/backups/phone_dedupe_<timestamp>.json` until the
   cleanup is verified in production.

> Always run the dry run against **production** before applying there — a
> development database may not reflect the real duplicate volume or formats.

## Rollback

To restore a cleared number, look up the user `_id` in the backup file and set
its `phone_number` back to the recorded value. Restore only one record per
number, otherwise the unique index will reject the write.

## The Unique Index

```js
db.users.createIndex(
  { phone_number: 1 },
  {
    name: "uniq_phone_number",
    unique: true,
    partialFilterExpression: { phone_number: { $type: "string" } },
  }
)
```

The `partialFilterExpression` is required: a plain unique index would allow only
one `null` document, and a sparse index would not help because the application
stores `phone_number: null` explicitly rather than omitting the field.

## Runtime Enforcement (API)

Submitting a number already owned by another account returns **409 Conflict**
(`"This phone number is already in use by another account."`). This applies to
`PATCH /v2/history/users/phone-number` and the user-creation flows. Re-submitting
your own number is a no-op, not a conflict. The app checks before writing and
also maps a unique-index `E11000` to the same 409, closing the check-then-write
race. The app re-creates `uniq_phone_number` on startup (idempotent).

## Deploy Order

Run the migration **before** deploying the enforcing code: the unique index
cannot build while duplicates exist. If code is deployed first against a dirty
database, startup logs an index warning and skips it — the app-level check still
works, but the DB guarantee is absent until the migration runs.
