# WakilAI Database Architecture

## Overview
WakilAI uses MongoDB as the primary database for storing user data, chat history, promo codes, subscriptions, and rate limiting information. This document provides a comprehensive overview of the collections, their schemas, relationships, and how the system works.

---

## Collection Names (Defaults)
Collection names are configurable via environment variables (see `.env.example` and `src/core/config.py`).
The table below lists the **default** collection names used by the API.

| Purpose | Default name | Setting |
| --- | --- | --- |
| Users | `users` | `USERS_COLLECTION` |
| Sessions | `sessions` | `SESSIONS_COLLECTION` |
| Messages | `messages` | `MESSAGES_COLLECTION` |
| Files | `files` | `FILES_COLLECTION` |
| Rate limits / credits | `creditusage` | `RATE_LIMIT_COLLECTION` |
| Promo codes | `promos` | `PROMO_CODE_COLLECTION` |
| User promo codes | `user-promos` | `USER_PROMO_CODE_COLLECTION` |
| Transactions | `transactions` | `TRANSACTION_COLLECTION` |
| Subscriptions | `subscriptions` | `SUBSCRIPTIONS_COLLECTION` |
| Daily subscriptions | `daily_subscriptions` | `DAILY_SUBSCRIPTIONS_COLLECTION` |
| Token counts | `token_counts` | `TOKEN_COUNTING_COLLECTION` |
| Telegram chats | `telegram_chats` | `TELEGRAM_CHATS_COLLECTION` |
| Referral sources | `referral_sources` | `REFERRAL_SOURCES_COLLECTION` |
| Projects | `projects` | `PROJECTS_COLLECTION` |
| Project members | `project_members` | `PROJECT_MEMBERS_COLLECTION` |
| Project invites | `project_invites` | `PROJECT_INVITES_COLLECTION` |

---

## Collections Structure

### 1. **users** Collection (default: `users`)
Stores user profiles and auth identifiers.

**Schema:**
```javascript
{
  "_id": String,                  // Internal user_id
  "user_id": String,              // Duplicate for querying
  "username": String,
  "first_name": String,
  "last_name": String,
  "phone_number": String,
  "picture": String,
  "web_client": String,           // wakilai / birdarcha
  "external_id": String,          // DT external ID (optional)
  "is_blocked": Boolean,
  "blocked_at": DateTime,
  "blocked_reason": String,
  "unblocked_at": DateTime,
  "created_at": DateTime,
  "updated_at": DateTime
}
```

**Indexes:**
- `_id` (default)
- `user_id`

---

### 2. **sessions** Collection (default: `sessions`)
Stores chat sessions (conversation threads).

**Schema:**
```javascript
{
  "_id": String,                  // Session ID (ses-...)
  "session_id": String,           // Same as _id
  "user_id": String,              // Reference to users._id
  "title": String,                // Session title
  "tags": Array,                  // Optional tags
  "status": String,               // draft | active
  "activated_at": DateTime,       // First activation timestamp
  "created_at": DateTime,
  "updated_at": DateTime
}
```

**Indexes:**
- `user_id`, `status`, `updated_at`

---

### 3. **messages** Collection (default: `messages`)
Stores chat messages (query + response) with metadata.

**Schema:**
```javascript
{
  "_id": ObjectId,
  "user_id": String,              // Reference to users.user_id
  "session_id": String,           // Chat session identifier
  "message_id": String,           // Unique message identifier
  "role": String,                 // "user" or "assistant"
  "content": String,              // Message text
  "assistant_type": String,       // e.g. "main", "tax", "court"
  "model": String,                // LLM model used (e.g., "gpt-oss-120b")
  "timestamp": DateTime,          // Message timestamp
  "metadata": {
    "assistant": String,          // main / soliq / deepresearch
    "stream": Boolean,
    "latency_ms": Number,
    "token_usage": {
      "input_token": Number,
      "context_token": Number,
      "output_token": Number,
      "embedding_input_token": Number
    },
    "attachments": Array
  },
  "feedback_type": String,        // positive / negative
  "feedback_content": String,     // Optional feedback text
  "share_id": String,
  "shared": Boolean,
  "shared_at": DateTime,
  "shared_by": String,
  "created_at": DateTime,
  "updated_at": DateTime
}
```

**Indexes:**
- `session_id`, `created_at`
- `message_id`

---

### 4. **files** Collection (default: `files`)
Stores uploaded files, OCR results, and storage metadata.

**Schema:**
```javascript
{
  "_id": String,                  // File ID
  "file_id": String,
  "user_id": String,
  "message_id": String | null,
  "scope": "message",
  "file_url": String,             // GCS public URL (or signed access)
  "ocr_result": String,
  "file_metadata": {
    "file_name": String,
    "file_type": String,
    "file_size": Number,
    "gcs_path": String,
    "file_content_hash": String,
    "ocr_token_count": Number,
    "milvus_file_index": {
      "enabled": Boolean,
      "collection": String,
      "chunk_count": Number,
      "embedding_model": String,
      "error": String
    }
  },
  "status": String,               // pending / processing / completed / failed
  "created_at": DateTime,
  "updated_at": DateTime
}
```

**Indexes:**
- `_id` (file_id)
- `user_id`
- `message_id`

---

### 5. **Feedback (embedded in messages)**
Feedback is stored directly on the `messages` documents rather than a separate
collection.

**Fields:**
- `feedback_type` (positive / negative)
- `feedback_content` (optional text)
- `updated_at` (timestamp of feedback update)

---

### 6. **rate_limits** Collection (default: `creditusage`)
Tracks daily credit usage for users (base limits + promo/subscription credits).

**Schema:**
```javascript
{
  "_id": ObjectId,
  "user_id": String,              // Reference to users.user_id
  "date": String,                 // Date in "YYYY-MM-DD" format
  "credits_used": Number,         // Credits consumed today
  "created_at": DateTime,         // First request timestamp
  "updated_at": DateTime          // Last request timestamp
}
```

**Indexes:**
- `user_id` + `date` (compound, unique)
- `date` (for cleanup of old records)

**How it works:**
- **Resets daily**: Each day gets a new document
- **Default limit**: 100 credits per day (configurable via `DAILY_CREDITS_LIMIT`)
- **Credit costs**: Per assistant, see `CREDIT_COST_*` in `src/core/config.py` (e.g. main, tax, court).

**Example calculation:**
- User with 100 daily credits can make any mix of requests whose per-request costs sum to at most 100 for that day.

---

### 7. **promo_codes** Collection (default: `promos`)
Defines promo codes with their properties.

**Schema:**
```javascript
{
  "_id": ObjectId,
  "code": String,                 // Unique promo code (e.g., "PREMIUM2026")
  "is_active": Boolean,           // Whether code can be used
  "expiration_date": DateTime,    // Expiration date (null = forever)
  "credit_amount": Number,        // Daily credits granted (null = unlimited)
  "created_at": DateTime,         // Code creation time
  "created_by": String,           // Admin who created it
  "description": String,          // Optional description
  "updated_at": DateTime          // Last modification time
}
```

**Indexes:**
- `code` (unique)
- `is_active`

**Promo Code Types:**

| expiration_date | credit_amount | Meaning |
|-----------------|---------------|---------|
| `null` | `null` | Unlimited credits forever |
| `2026-12-31` | `null` | Unlimited credits until Dec 31, 2026 |
| `null` | `500` | 500 daily credits forever |
| `2026-06-30` | `200` | 200 daily credits until June 30, 2026 |

---

### 8. **user_promo_codes** Collection (default: `user-promos`)
Links users to their assigned promo codes.

**Schema:**
```javascript
{
  "_id": ObjectId,
  "user_id": String,              // Reference to users.user_id (unique)
  "promo_code": String,           // Reference to promo_codes.code
  "assigned_at": DateTime,        // When code was assigned
  "total_credits_granted": Number,// Daily credits from promo (null = unlimited)
  "total_credits_used": Number    // Total credits used (for tracking)
}
```

**Indexes:**
- `user_id` (unique) - **One user = One promo code**
- `promo_code`

**Important Notes:**
- **One user can only have ONE promo code at a time**
- Assigning a new promo code replaces the old one
- Promo code credits override default daily limits
- If promo code expires, user falls back to default limits

---

### 9. **subscriptions** Collection (default: `subscriptions`)
Tracks active subscription entitlements.

**Schema:**
```javascript
{
  "user_id": String,
  "tier": String,
  "period": String,
  "daily_credits": Number,
  "days": Number,
  "total_credits": Number,
  "amount_sum": Number,
  "start_ms": Number,
  "end_ms": Number,
  "last_order_id": String,
  "last_transaction_id": String,
  "provider": String,
  "created_at_ms": Number,
  "updated_at_ms": Number
}
```

### 10. **daily_subscriptions** Collection (default: `daily_subscriptions`)
Stores daily-pass subscriptions (same schema as `subscriptions`).

### 11. **transactions** Collection (default: `transactions`)
Payment provider transaction records (Payme/Click).

**Schema:**
```javascript
{
  "id": String,
  "user": ObjectId | String,
  "state": Number,
  "amount": Number,
  "provider": String,
  "create_time": Number,
  "perform_time": Number,
  "cancel_time": Number,
  "reason": Number,
  "createdAt": DateTime,
  "updatedAt": DateTime
}
```

### 12. **referral_sources** Collection (default: `referral_sources`)
Tracks referral sources for the web client.

**Schema:**
```javascript
{
  "source": String,
  "total_count": Number,
  "first_seen_at": DateTime,
  "last_seen_at": DateTime
}
```

### 13. **telegram_chats** Collection (default: `telegram_chats`)
Stores Telegram chat IDs for admin/broadcast operations.

**Schema:**
```javascript
{
  "chat_id": Number,
  "user_id": Number,
  "username": String,
  "first_name": String,
  "last_name": String,
  "language_code": String,
  "is_active": Boolean,
  "created_at_ms": Number,
  "updated_at_ms": Number
}
```

### 14. **token_counts** Collection (default: `token_counts`)
Reserved for token usage snapshots. Current implementation stores token usage inside
`messages.metadata.token_usage`.

### 15. **projects** Collection (default: `projects`)
Legal project workspaces (documents, sessions, RAG). Single **owner** per project (`owner_id`).

**Schema:**
```javascript
{
  "_id": String,              // proj-...
  "owner_id": String,
  "title": String,
  "files": [String],
  "status": String,           // active | archived | closed
  "stats": { "docs": Number, "chats": Number, "reminders": Number },
  "settings": Object,
  "created_at": DateTime,
  "updated_at": DateTime
}
```

### 16. **project_members** Collection (default: `project_members`)
Non-owner collaborators. Owner is not stored here (see `projects.owner_id`).

**Schema:**
```javascript
{
  "_id": String,              // pmem-...
  "project_id": String,
  "user_id": String,
  "role": "member",
  "joined_at": DateTime,
  "invited_by": String,
  "invite_id": String         // optional pinv-... audit
}
```

**Indexes:** unique `(project_id, user_id)`; `(user_id, joined_at)`.

### 17. **project_invites** Collection (default: `project_invites`)
Single-use invitation links (`pinv-...`). Default TTL: `PROJECT_INVITE_TTL_HOURS` (168h).

**Schema:**
```javascript
{
  "_id": String,              // pinv-... (token in join URL)
  "project_id": String,
  "created_by": String,
  "status": String,           // pending | accepted | revoked | expired
  "expires_at": DateTime,
  "accepted_by": String,
  "accepted_at": DateTime,
  "created_at": DateTime
}
```

---

### 18. **workflow_states** Collection (default: `workflow_states`)
The board columns an organization defines, seeded with five defaults per board.

`category` is the load-bearing field: `name` is user-typed in any language, so
no system logic may branch on it. Closure checks, dashboards and reminders read
`category` instead, which is what lets an org rename "Done" to "Yakunlandi" or add
"Sudda" without breaking anything.

**Schema:**
```javascript
{
  "_id": String,              // wfst-...
  "org_id": String,
  "applies_to": String,       // case | task — separate boards
  "name": String,             // user-editable label
  "category": String,         // draft | in_progress | in_review | returned | closed
  "order": Number,            // board position
  "is_system": Boolean,       // seeded default: renameable, NOT deletable
  "archived": Boolean,
  "created_at": DateTime,
  "updated_at": DateTime
}
```

**Indexes:**
- `(org_id, applies_to, name)` unique, partial on `archived != true`
- `(org_id, applies_to, order)`

Seeded lazily: `list_states` seeds any board that reads back empty, so
organizations created before this feature heal on first read without a migration.
Because seeding is lazy, two first-requests can race; the loser's upsert hits the
unique index, and `DuplicateKeyError` is caught and skipped rather than surfacing
as a 500.

`assert_state_usable` — the one validator every member-supplied `state_id` passes
through — **refuses a `closed`-category column**. `state_id` is otherwise an
ordinary editable field, so without that refusal an assignee could `PATCH` a Case
straight onto "Done": it would read as closed to every dashboard and reminder
(they key on `category`) while `status` stayed `active` and no admin ever signed
it off. The approval path reaches the closed column through `default_state_for`,
not through this validator, so closure approval remains its only writer.

Only a *change* to `state_id` is validated. A client that PATCHes a whole form
back sends the state it just displayed, and on a closed item that value is the
closed column — validating the no-op would make every other field of a closed
Case or Task uneditable, and would log a `state_changed` event for a move that
never happened.

A **closed item's board position is frozen**: a `state_id` change on a Case or
Task whose `closure.approved_at` is set returns 409. Since `assert_state_usable`
refuses the closed column, allowing a move *off* it would be one-way — the item
would show as in progress while `status` said closed, and neither a PATCH back
nor `approve_closure` (which refuses to run twice) could restore it. The way out
is `POST /closure/reopen`, admin only: it clears the closure block outright,
returns `status` to `active`, and moves the item back to the board's draft
column. `closure.requested_at` is cleared with the rest, so the next closure
starts from a fresh request rather than letting an admin approve one nobody made.
Reopening is recorded as `closure.reopened` — a new event, never an edit of the
approval it undoes.

`state_id` also cannot be cleared. A null would drop the item off every board and
out of `count_using_state`, letting `archive_state` delete a column that still
holds work.

---

### 19. **Cases (embedded in `projects`)**
A **Case is a `projects` document with `org_id` set**. There is no `cases`
collection. This reuses the whole existing pipeline — GCS upload, SHA-256 dedup,
OCR, Milvus indexing, vector search, project-scoped sessions, per-project
instructions — unchanged, including the parts that live in `rest-api-llm`, whose
`POST /api/v1/project/search` is keyed on `project_id`.

The id stays `proj-...`; only the API and UI say "case".

**Additional fields on an enterprise Case:**
```javascript
{
  "org_id": String,           // set => this project is a Case
  "description": String,
  "objective": String,        // success criteria, stays visible
  "case_type": String,        // AssistantType key: main | tax | court | ...
  "state_id": String,         // workflow_states, applies_to: case
  "start_date": Date,
  "deadline": Date,
  "assignee_id": String,      // must be an active org member
  "suspect": String,          // optional
  "victim": String,           // optional
  "updated_by": String,
  "closure": {                // requested by owner/assignee, approved by admin
    "requested_by": String, "requested_at": DateTime,
    "approved_by": String,  "approved_at": DateTime
  }
}
```

**Separation rules — the price of the reuse, all three required:**
- `list_projects` filters personal scope under `$and` (a dict cannot hold two
  top-level `$or` keys). Rows predating Cases have no `org_id` key, so "personal"
  matches absent as well as null.
- `assert_project_access` resolves a Case through `organization_members`, never
  `project_members` — which stays reserved for the per-Case access-control
  customization.
- `assert_project_owner` and the project-invite guard 404 on a Case, so the legacy
  `/history/projects` surface cannot close, archive, or mint invites for one
  behind the closure workflow. `assert_project_access` also 404s an **archived**
  Case, or its files, sessions and instructions would stay live on that surface
  after an admin archived it.

A chat session opened on a Case carries the Case's `org_id`. Without it the row
would be personal-scope, and `assert_session_access` only re-checks membership
when `org_id` is set — so a member removed from the organization would keep read
and write on every Case conversation they had opened.

Archiving a Case archives its Tasks in the same call, and each one gets its own
`task.archived` event carrying `{"cascade": "case.archived"}` — a bulk update
alone would leave every Task's timeline silent at the moment it was archived.
Left behind, the Tasks would keep appearing in the org-wide task list pointing at
a Case that 404s, and would keep blocking `archive_state` with a count nobody
could act on.

Each Task is claimed with its own conditional write and logged only if that write
is the one that archived it. A read-then-`update_many` would be shorter, but the
read is a stale snapshot: an admin archiving a Task individually in that window
would earn a second, misattributed event blaming the cascade. An evidence log may
miss nothing, and may also invent nothing. The claim loop is batched, so a Case
with any number of Tasks is covered.

The Case is written before its Tasks, so `create_task`'s access check starts
failing at once. That check and its insert are not one atomic step, so a create
already in flight can still land after the cascade has drained the collection —
`create_task` closes that window itself by re-reading the Case's `archived` flag
after its insert and deleting the row if it lost the race (deleted, not archived:
no event has been logged yet, so the Task never existed as far as the record is
concerned). The ordering also means a failed cascade leaves the Case archived
with live Tasks, so `archive_case` loads its row **without** the active-only
filter: `DELETE` is idempotent, and repeating it finishes an interrupted cascade
instead of 404ing. The second call logs no duplicate `case.archived` event.

Archiving a Case that was already **closed** leaves `status: "closed"` alone and
only sets `archived`. `archived` is the flag every read filters on; `status` is
the lifecycle marker, and there is no un-archive route to recover it — overwriting
it would erase the fact that the work was signed off, permanently.

`start_date` and `deadline` are stored as **BSON dates, not strings**. BSON
compares across types by type order rather than by value, so a string deadline
would never match a `datetime` range bound and the `(org_id, deadline)` index
would return nothing for every reminder query. `clean_for_mongodb` converts a
`datetime.date` to midnight UTC on the way in.

**Indexes** (on the shared `projects` collection): `(org_id, state_id,
assignee_id)` and `(org_id, deadline)`, both partial on `org_id` **existing**.
The partial filter keeps the rows predating Cases out. It deliberately uses
`$exists` and not the tighter `$type: "string"` — MongoDB only uses a partial
index when the query is provably a subset of the filter, and it does not infer
"is a string" from an equality match, so a `$type` filter yields an index the
planner refuses to touch and every Case query falls back to a collection scan
over every personal project in the deployment. Confirm with `explain()` after
changing these.

This is also why `create_project` writes **no `org_id` key at all** for a personal
project rather than an explicit `null`. A null is a value, `$exists` matches it,
and every personal project in the deployment would land back in both Case indexes
— the exact write cost the partial filter exists to avoid. `is_case` and
`PERSONAL_SCOPE` both already read absence as personal, so nothing is lost.

`AccountArchiveService` scopes **four** sweeps to personal rows — `projects`,
`sessions`, `messages` and `files`. Unscoped, a member deleting their own account
takes the whole organization's data with them: the Cases they opened off the
board, the Case conversations they started, those conversations' transcripts, and
the evidence files they uploaded. Every one of those losses is visible, because
`get_sessions_by_project`, `get_messages` and `get_file_by_id` all filter archived
rows — the Case would keep its session list while every transcript came back empty
and every file 404'd on open.

One predicate separates all four because all four carry `org_id`. Sessions get it
at creation; messages inherit it from their session, and a message's attachments
are stamped at the same moment (a file has no session, and so no org, until it is
attached); a project-scoped file reads it from its project row inside
`add_file_upload` rather than taking it from the caller, because the upload path
is long and a branch that forgot to pass it would lose the org's evidence
silently. Rows written before this stamp existed have no `org_id` and read as
personal, which is what they were.

---

### 20. **tasks** Collection (default: `tasks`)
Assignments within a Case. `org_id` is copied from the parent Case, never taken
from the request, so the two cannot disagree.

**Schema:**
```javascript
{
  "_id": String,              // task-...
  "case_id": String,          // a proj-... id (the Case)
  "org_id": String,           // denormalized for org-wide queries
  "title": String,
  "description": String,
  "objective": String,
  "task_type": String,        // AssistantType key
  "state_id": String,         // workflow_states, applies_to: task
  "start_date": Date,
  "deadline": Date,
  "assignee_id": String,
  "created_by": String,
  "updated_by": String,
  "closure": Object,          // same shape as a Case's
  "archived": Boolean,
  "created_at": DateTime,
  "updated_at": DateTime
}
```

**Indexes:** `(case_id)`, `(org_id, assignee_id, state_id)`, `(org_id, deadline)`

---

### 21. **activity_logs** Collection (default: `activity_logs`)
Append-only record of who did what. Evidence under Law ZRU-1115, not application
state: **no updates, no deletes, no `archived` flag.** Corrections are new events.

`actor.role` and `actor.name` are snapshots taken at write time — roles change and
members leave, and the log must stay readable years later without a join.

**Schema:**
```javascript
{
  "_id": String,              // alog-... (uuid7: sorts chronologically)
  "org_id": String,
  "occurred_at": DateTime,
  "event_type": String,       // object.verb, closed set
  "actor": {
    "id": String,
    "type": String,           // user | agent | system
    "role": String,           // SNAPSHOT at event time
    "name": String            // SNAPSHOT at event time
  },
  "on_behalf_of": String,     // the human behind an agent action
  "object": { "type": String, "id": String, "label": String },
  "case_id": String,          // nullable, for case timeline queries
  "task_id": String,          // nullable
  "payload": Object           // {from, to} on state changes, {fields} on edits
}
```

**Indexes:** `(org_id, _id desc)`, `(org_id, case_id, _id desc)`, `(actor.id, _id desc)`

Pagination is a `$lt` cursor on `_id`. Note that `db.find_documents` always applies
`.sort("created_at", -1)`, and these rows have no `created_at`, so reads go through
the collection directly.

---

## System Architecture & Data Flow

### Access & User Initialization Flow

```
1. Client authenticates via API key (or DT API key for DT integration)
   ↓
2. User logs in via Telegram/Google/DT auth endpoint
   ↓
3. System creates/updates record in `users` collection
   ↓
4. Client receives internal user_id and uses it in subsequent requests
   ↓
5. Chat sessions are created in `sessions` collection as needed
```

### Chat Request Flow

```
1. User sends question
   ↓
2. System validates API key and checks the session in `sessions`
   ↓
3. Rate Limit & Credit Check:
   │
   ├─→ Check if user has promo code (`user_promo_codes`)
   │   │
   │   ├─→ Has promo code?
   │   │   ├─→ Check expiration_date (from `promo_codes`)
   │   │   ├─→ Expired? → Use default rate limits
   │   │   └─→ Valid? → Use promo credit_amount
   │   │       ├─→ credit_amount = null → Unlimited
   │   │       └─→ credit_amount = N → N daily credits
   │   │
    │   └─→ No promo code?
    │       └─→ Check `creditusage` (rate_limits) for today
   │           ├─→ credits_used < 100 → Allow
   │           └─→ credits_used >= 100 → Block (429 error)
   │
4. Deduct credits based on assistant type (see `AssistantConfig` / `settings.ASSISTANTS`):
   ├─→ Main (umumiy)
   ├─→ Tax (soliq)
   └─→ Court and other specialists
   ↓
5. Process request (retrieve documents, generate answer)
   ↓
6. Save query/response payload to `messages` (content.query/content.response)
   ↓
7. Return response to client
```

### File Upload Flow

```
1. User uploads file
   ↓
2. System uploads to Google Cloud Storage
   ↓
3. System performs OCR extraction
   ↓
4. File metadata + OCR result saved to `files` collection
   ↓
5. User can query file using file_id
   ↓
6. System uses OCR result as context for answering
```

### Promo Code Assignment Flow

```
1. Admin creates promo code in `promo_codes`
   ↓
2. Admin assigns code to user
   ↓
3. System validates:
   ├─→ Promo code exists?
   ├─→ Promo code active?
   └─→ Valid? → Proceed
   ↓
4. System checks `user_promo_codes` for existing assignment
   ├─→ Exists? → Update to new promo code
   └─→ Not exists? → Create new assignment
   ↓
5. User now uses promo code credits instead of default limits
```

### Rate Limiting Logic

**For users WITHOUT promo codes:**
```javascript
// Check today's usage in creditusage collection
const today = "2026-01-01";
const limit = await db.creditusage.findOne({ user_id, date: today });

if (!limit) {
  // First request today - allow and create record
  credits_remaining = 100 - credit_cost;
} else {
  // Check remaining credits
  credits_remaining = 100 - limit.credits_used;
  
  if (credits_remaining < credit_cost) {
    // Block request - insufficient credits
    throw new Error("Insufficient credits");
  }
  
  // Deduct credits
  await db.creditusage.updateOne(
    { user_id, date: today },
    { $inc: { credits_used: credit_cost } }
  );
}
```

**For users WITH promo codes:**
```javascript
// Get user's promo code assignment
const assignment = await db.user_promo_codes.findOne({ user_id });
const promo = await db.promo_codes.findOne({ code: assignment.promo_code });

// Check expiration
if (promo.expiration_date && now > promo.expiration_date) {
  // Expired - use default rate limits
  // ... (same as above)
}

// Check credit amount
if (promo.credit_amount === null) {
  // Unlimited credits - allow all requests
  return { allowed: true, remaining: -1 };
}

// Use promo code daily limit instead of default 100
const today = "2026-01-01";
const limit = await db.creditusage.findOne({ user_id, date: today });
const daily_limit = promo.credit_amount; // e.g., 500

if (!limit) {
  credits_remaining = daily_limit - credit_cost;
} else {
  credits_remaining = daily_limit - limit.credits_used;
  
  if (credits_remaining < credit_cost) {
    throw new Error("Insufficient credits");
  }
  
  await db.creditusage.updateOne(
    { user_id, date: today },
    { $inc: { credits_used: credit_cost } }
  );
}
```

---

## Credit System Details

### Default Credits (No Promo Code)
- **Daily Limit**: 100 credits
- **Resets**: Every day at midnight UTC
- **Tracked in**: `creditusage` collection

### Promo Code Credits
- **Daily Limit**: Defined by `promo_codes.credit_amount`
  - `null` = Unlimited
  - Number = Custom daily limit (e.g., 500 credits/day)
- **Expiration**: Defined by `promo_codes.expiration_date`
  - `null` = Forever
  - DateTime = Expires at specified date
- **Tracked in**: `creditusage` collection (same as default, but different limit)

### Credit Costs by Assistant Type
Costs are defined in `settings.ASSISTANTS` (see `src/core/config.py`). Typical entries include main (umumiy), tax (soliq), court, and contract analyzer.

**Example:** With main at 10 credits per turn, a user with 100 daily credits can make up to 10 main-only requests if no other assistants are used.

---

## Relationships Diagram

```
users (1) ←──→ (0..1) user_promo_codes ←──→ (1) promo_codes
  │
  ├──→ (0..*) sessions
  ├──→ (0..*) messages
  ├──→ (0..*) files
  ├──→ (0..*) creditusage (rate_limits)
  ├──→ (0..1) subscriptions
  ├──→ (0..1) daily_subscriptions
  ├──→ (0..*) transactions
  ├──→ (0..*) token_counts
  └──→ (0..*) projects

referral_sources (global)
telegram_chats (global)
```

**Cardinality:**
- One user can have **zero or one** promo code
- One user can have **many** sessions
- One user can have **many** messages
- One user can have **many** files
- One user can have **many** credit usage records (one per day)
- One user can have **zero or one** subscription and daily subscription
- One user can have **many** transactions
- One user can have **many** projects
- `referral_sources` and `telegram_chats` are global collections (not user-scoped)

---

## Configuration Settings

All configurable via environment variables in `.env`:

### Credit System
```bash
DAILY_CREDITS_LIMIT=100              # Default daily credits for users
CREDIT_COST_MAIN_ASSISTANT=10        # Credits for main assistant
CREDIT_COST_SOLIQ_ASSISTANT=15       # Credits for soliq assistant
```

### Database
```bash
MONGODB_URI=mongodb://localhost:27017
MONGODB_DB_NAME=wakilai
USERS_COLLECTION=users
SESSIONS_COLLECTION=sessions
MESSAGES_COLLECTION=messages
FILES_COLLECTION=files
PROJECTS_COLLECTION=projects
PROJECT_MEMBERS_COLLECTION=project_members
PROJECT_INVITES_COLLECTION=project_invites
PROJECT_INVITE_TTL_HOURS=168
PROMO_CODE_COLLECTION=promos
USER_PROMO_CODE_COLLECTION=user-promos
RATE_LIMIT_COLLECTION=creditusage
TRANSACTION_COLLECTION=transactions
SUBSCRIPTIONS_COLLECTION=subscriptions
DAILY_SUBSCRIPTIONS_COLLECTION=daily_subscriptions
TOKEN_COUNTING_COLLECTION=token_counts
TELEGRAM_CHATS_COLLECTION=telegram_chats
REFERRAL_SOURCES_COLLECTION=referral_sources
```

### Session Management
```bash
TELEGRAM_SESSION_TIMEOUT=259200      # 3 days in seconds
```

---

## API Endpoints

### Promo Codes
- `POST /api/admin/promo-codes` - Create promo code
- `GET /api/admin/promo-codes` - List all promo codes
- `GET /api/admin/promo-codes/{code}` - Get specific promo code
- `PATCH /api/admin/promo-codes/{code}/activate` - Activate promo code
- `PATCH /api/admin/promo-codes/{code}/deactivate` - Deactivate promo code
- `DELETE /api/admin/promo-codes/{code}` - Delete promo code

### User Promo Code Assignment
- `POST /api/admin/promo-codes/assign` - Assign promo code to user
- `DELETE /api/admin/promo-codes/assign/{user_id}` - Remove user's promo code
- `GET /api/admin/promo-codes/users` - List all users with promo codes
- `GET /api/admin/promo-codes/users/{user_id}` - Get user's promo code

### Chat
- `POST /api/chat/ask` - Ask question to main or soliq assistant
- `POST /api/chat/ask-file` - Ask question about uploaded file
- `POST /api/chat/agent` - Agentic RAG (if exposed on your deployment)
- `POST /api/chat/agent/stream` - Streaming two-stage RAG (same pipeline as main; billed as main)

---

## Data Retention & Cleanup

### Automatic Cleanup (Recommended)
- **creditusage**: Delete records older than 30 days
- **messages**: Archive messages older than 1 year
- **sessions**: Prune inactive sessions if needed

### Manual Cleanup
MongoDB TTL indexes can be used for automatic cleanup:

```javascript
// Auto-delete creditusage after 30 days
db.creditusage.createIndex(
  { "created_at": 1 }, 
  { expireAfterSeconds: 2592000 } // 30 days
)
```

---

## Security Considerations

1. **API Keys**: Most endpoints require the API key header; DT endpoints require the DT key.
2. **Rate Limiting**: Prevents abuse via the credit system.
3. **Promo Code Validation**: Checks active status and expiration.
4. **OAuth Flows**: Google/Telegram auth endpoints create user records.
5. **Admin Endpoints**: Protected by admin API key and body secret where applicable.

---

## Future Enhancements

1. **Payment Integration**: Link promo codes to payment system
2. **Usage Analytics**: Track usage patterns per user/assistant
3. **Credit Top-ups**: Allow users to purchase additional credits
4. **Tiered Plans**: Different credit limits for different subscription tiers
5. **Referral System**: Bonus credits for referrals
6. **Credit History**: Detailed log of all credit transactions

---

**Last Updated**: January 1, 2026
**Version**: 5.0.0
