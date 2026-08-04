## 3. Case lifecycle

### 3.1 Create

`POST /api/v2/organizations/{org_id}/cases`

| Step | Where |
|---|---|
| Route | `src/api/v2/cases.py:56` |
| Actor comes from the JWT, never the body | `src/api/v2/cases.py:20` |
| Service | `src/services/case_service.py:186` |
| Must be an org member | `case_service.py:189` |
| Assignee must be a member too | `case_service.py:161` |
| Pick the board column | `case_service.py:172` (`_resolve_state`) |
| Board seeded on first use | `workflow_state_service.py:68` |
| Insert, **then** log | `case_service.py:229`, `case_service.py:231` |

The log is written **after** the insert succeeds — never log something that did not
happen.

**Records to look at:**

```js
db.projects.findOne({_id: "proj-..."})       // org_id, state_id, closure: {}, status: "active"
db.activity_logs.find({case_id: "proj-..."}) // one event: case.created
db.workflow_states.find({org_id: "org-..."}) // 10 rows: 5 case columns + 5 task columns
```

### 3.2 Read

- List: route `src/api/v2/cases.py:68`, service `case_service.py:235`
- One: route `src/api/v2/cases.py:94`, service `case_service.py:259`
- Access check on every call: `case_service.py:140` — membership is re-read each
  time, so removing a member revokes access immediately.

### 3.3 Edit

`PATCH /organizations/{org_id}/cases/{case_id}` — route `src/api/v2/cases.py:100`,
service `case_service.py:262`.

Who may edit: owner, assignee, or an admin — `case_service.py:147`.

Two guards worth showing your mentor:

- `case_service.py:286` — an explicit `state_id: null` is rejected. Letting it
  through would drop the Case off every board *and* out of `count_using_state`, so
  `archive_state` would then delete a column that still holds work.
- `case_service.py:302` — a **closed** Case's board position is frozen (409). The
  closed column is not `assert_state_usable`-able, so allowing a move off it would be
  one-way. Reopen is the way out.

### 3.4 Close

Two steps, two people (or one admin doing both — a small firm has one Head).

| Step | Route | Service |
|---|---|---|
| Request closure | `src/api/v2/cases.py:122` | `case_service.py:376` |
| Approve closure | `src/api/v2/cases.py:130` | `case_service.py:395` (admin only) |

`approve_closure` is the **single writer of the closed lifecycle**: it sets
`closure.approved_at`, `status: "closed"`, and moves the Case to the closed column in
the same write (`case_service.py:427`). Matched by **category**, never by name — the
column can be renamed.

```js
db.projects.findOne({_id:"proj-..."}, {status:1, state_id:1, closure:1})
db.activity_logs.find({case_id:"proj-..."}).sort({occurred_at:1})
// ... case.created, closure.requested, closure.approved
```

### 3.5 Reopen

`POST .../closure/reopen` — route `src/api/v2/cases.py:138`, service
`case_service.py:441`. Admin only.

Clears the **whole** closure block including `requested_at`, sets `status: "active"`,
moves back to the draft column, logs `closure.reopened`. `requested_at` goes too, so
the next closure starts from a fresh request instead of letting an admin approve
something nobody asked for.

### 3.6 Archive (soft delete)

`DELETE .../cases/{case_id}` — route `src/api/v2/cases.py:115`, service
`case_service.py:332`. Admin only. Nothing is removed from Mongo.

Order matters, and it is the most interesting part of the feature:

1. Load the row **without** the active-only filter (`case_service.py:347`) — so a
   retry can finish an interrupted cascade instead of 404ing.
2. Write `archived: true`. `status` is left alone if the Case was already **closed**
   (`case_service.py:361`) — `archived` is what reads filter on, `status` is the
   lifecycle marker, and there is no un-archive route to recover it.
3. **Log before the cascade** (`case_service.py:364`). The guard keys off the
   pre-write snapshot, so logging after a cascade that throws would lose the event
   for good — the retry would see `archived: true` and skip it.
4. Cascade to Tasks (`case_service.py:373` → `task_service.py:332`).

---

## 4. Task lifecycle

Same shape as a Case. Differences worth pointing at:

| Step | Route | Service |
|---|---|---|
| Create | `src/api/v2/tasks.py:48` | `task_service.py:122` |
| List in a Case | `src/api/v2/tasks.py:64` | `task_service.py:192` |
| List org-wide ("my work") | `src/api/v2/tasks.py:91` | `task_service.py:192` |
| Edit | `src/api/v2/tasks.py:119` | `task_service.py:237` |
| Archive | `src/api/v2/tasks.py:131` | `task_service.py:297` |
| Closure request / approve / reopen | `tasks.py:138` / `:145` / `:152` | `task_service.py:379` / `:398` / `:306` |

**`org_id` is copied from the parent Case, never from the request body**
(`task_service.py:128` loads the Case, `:155` copies it), so the two cannot disagree.

**The race check** — `task_service.py:182`. The access check and the insert are not
one atomic step, so a create already in flight can land *after* `archive_case` has
drained the collection. The Task is inserted, the Case's flag is re-read, and the row
is **deleted** if it lost the race (`task_service.py:183`). Deleted, not archived — no
event has been logged yet, so as far as the record is concerned it never existed.

**The cascade** — `task_service.py:332`. Batched loop, and each Task is claimed with
its own conditional `update_one`. It logs only if *that* write is the one that
archived it (`task_service.py:370`). A bulk `update_many` plus a loop over the rows
read beforehand would be shorter, but the read is a stale snapshot: an admin
archiving a Task individually in that window would produce a second, misattributed
`task.archived` event. An evidence log may miss nothing, and may invent nothing.

---

## 5. Activity log

Append-only. There is no update route and no delete route — a correction is a new
event. Read-only API: `src/api/v2/activity_logs.py:45` (list) and `:73` (one).

### How an event gets written

Two entry points:

- **`record()`** — `activity_log_service.py:42`. One explicit event.
  Wrapped by `_log()` helpers: `case_service.py:69`, `task_service.py:57`.
- **`record_changes()`** — `activity_log_service.py:93`. Turns one PATCH into the
  events a timeline actually needs.

`record_changes` splits an edit into up to three events:

| Changed | Event | Line |
|---|---|---|
| `state_id` | `case.state_changed` / `task.state_changed`, payload `{from, to}` | `activity_log_service.py:144` |
| `assignee_id` | `case.assigned` / `task.assigned`, payload `{from, to}` | `activity_log_service.py:151` |
| anything else | `case.updated` / `task.updated`, payload `{fields: [...]}` | `activity_log_service.py:161` |

`from` matters as much as `to` — a state change without the previous value is half a
fact. `metadata` is excluded from the field list: it is a free-form bag and listing
its keys says nothing.

### It never breaks the action

`record()` swallows every exception (`activity_log_service.py:86`). An audit trail
that can take down Case creation is worse than a gap in the trail. The write is
**awaited inside** the guard, not fired and forgotten — an unawaited coroutine that
raises after the response is invisible.

### Actor is snapshotted

`activity_log_service.py:163` — the actor's name and role are copied **into** the
event at write time. A member who later leaves, or is renamed, does not rewrite
history.

### Event types

`src/models/activity_logs.py:22` — the full list:

```
case.created  case.updated  case.state_changed  case.assigned  case.archived
task.created  task.updated  task.state_changed  task.assigned  task.archived
closure.requested  closure.approved  closure.reopened
```

**Records:**

```js
db.activity_logs.find({org_id:"org-..."}).sort({occurred_at:1})
db.activity_logs.find({case_id:"proj-..."})   // one Case's whole timeline
db.activity_logs.find({task_id:"task-..."})   // one Task's
db.activity_logs.findOne()                    // note actor: {id, name, role} snapshot
```

---

## 6. Workflow states (the board)

- The five columns are `DEFAULT_STATES` — `workflow_state_service.py:21`.
- Seeded on first use, not on org creation — `workflow_state_service.py:68`, called
  from `:141` and `:197`. Organizations made before this feature heal themselves on
  first read, with no migration script. `$setOnInsert`, so re-running never
  overwrites a column the org has since renamed or reordered.
- `assert_state_usable` (`:154`) **refuses the closed column**. Only
  `approve_closure` may put something there.
- `archive_state` (`:282`) refuses to remove a column that still holds work, and
  re-counts *after* the write, rolling back with 409 if work slipped in
  (`:345` `_count_in_use`).

---

## 7. Permission rules in one place

| Action | Who |
|---|---|
| Create a Case / Task | any active org member |
| Read | any active org member |
| Edit | owner, assignee, or admin — `case_service.py:147`, `task_service.py:98` |
| Request closure | owner, assignee, or admin |
| Approve closure | **admin** — `case_service.py:403` |
| Reopen | **admin** — `case_service.py:454` |
| Archive | **admin** — `case_service.py:346` |

Membership is re-read on every call, so removing someone revokes access at once.

---

## 8. Running it

Backend on `localhost:8000`. The `.http` files are the fastest demo — open and click
through in order:

```
http/organizations.http    # make an org first, everything else needs org_id
http/workflow_states.http  # see the seeded board
http/cases.http            # create → edit → close → reopen → archive
http/tasks.http
http/activity_logs.http    # the timeline, after all of the above
```

Every route takes its actor from the JWT (`Authorization: Bearer ...`). There is no
`user_id` parameter to spoof — `src/api/v2/cases.py:20`, `tasks.py:20`.

## 9. Testing it

```bash
.venv/bin/python -m pytest tests/test_cases.py tests/test_tasks.py \
    tests/test_workflow_states.py tests/test_activity_logs.py -q
```

| File | Covers |
|---|---|
| `tests/test_cases.py` | org scoping, permissions, closure, reopen, the separation from personal projects |
| `tests/test_tasks.py` | the same rules plus the cascade and the create/archive race |
| `tests/test_workflow_states.py` | seeding, the closed-column rule, archive-with-work |
| `tests/test_activity_logs.py` | append-only, actor snapshot, `record_changes` splitting |

Four failures in `tests/test_llm_service_client.py` are pre-existing and unrelated.

**Suggested live demo order** — creates a full timeline in one pass:

1. Create org → check `workflow_states` has 10 rows (5 per board × 2 boards)
2. Create Case → `db.projects.findOne()`, `db.activity_logs.find()`
3. Move it a column → see `case.state_changed` with `{from, to}`
4. Add two Tasks → `db.tasks.find({case_id})`
5. Request + approve closure → `status: "closed"`, on the closed column
6. Try to PATCH `state_id` → **409**, the board is frozen
7. Reopen → back on draft, `closure: {}`, `closure.reopened` logged
8. Archive the Case → Case *and* both Tasks flagged `archived`, cascade events carry
   `payload: {cascade: "case.archived"}`
9. `db.activity_logs.find({case_id}).sort({occurred_at:1})` — the whole story, in
   order, nothing removed
