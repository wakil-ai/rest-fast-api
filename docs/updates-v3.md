# API v3 — Release summary

**Release:** `v3.0.0` (2026-05-20)  
**Changelog:** [../CHANGELOG.md](../CHANGELOG.md)

## What v3 means

| Layer | Meaning |
| --- | --- |
| **Git tag / app version** | `3.0.0` (`settings.VERSION`) |
| **New HTTP prefix** | `/api/v3/chat/*` (LangGraph chat) |
| **Existing integrations** | `/api/v2/*` remains supported (history, auth, payments, promo codes, etc.) |

## New in v3

```http
POST /api/v3/chat/ask
admin: <API_KEY>
Content-Type: application/json

{
  "user_id": "...",
  "session_id": "...",
  "query": "...",
  "assistant": "main"
}
```

Same request/response models as v2 chat. v2 `POST /api/v2/chat/ask` delegates to the same service.

Optional **`project_id`** on chat requests scopes RAG to that project’s uploaded files and instructions.

## Legal projects (new)

Workspace API under **`/api/v2/history/projects`**:

- Create/list/update projects per user
- Upload and search files within a project (Milvus `project_files`)
- Project-specific instructions for agents
- Sessions bound to a project

Full endpoint table: [api-reference.md](api-reference.md#projects-legal-workspaces) · pipeline: [file-management.md](file-management.md)

## Breaking changes since v2.3.x (last month)

See [updates-v2.md](updates-v2.md) for path and auth migrations:

- Rate limit → `/api/v2/history/users/rate-limit/{user_id}`
- Promo codes → `/api/v2/promo-codes/*`
- No `super_secret_admin_key` in bodies
- Telegram admin → `x-super-admin-key` header only

## Related docs

- [api-reference.md](api-reference.md)
- [bitrix24-leads.md](bitrix24-leads.md)
- [rate-limiting.md](rate-limiting.md)
