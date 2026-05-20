# Changelog

All notable changes to the WakilAI REST API are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project tags releases as `vMAJOR.MINOR.PATCH` (previous tag: `v2.3.1`).

## [3.0.0] — 2026-05-20

Release window: **2026-04-20 → 2026-05-20** (~1 month).  
This release marks the **LangGraph orchestration** era and the public **`/api/v3/chat`** surface, while keeping **`/api/v2`** for history, auth, payments, and most integrations.

### Added

#### Chat & orchestration (v3)
- **`POST /api/v3/chat/ask`** — LangGraph-based general assistant (`main`); v2 `/api/v2/chat/ask` remains as a backward-compatible alias to the same pipeline.
- LangGraph orchestration with Redis checkpoints (`AsyncRedisSaver`), session/thread continuity, and follow-up query resolution.
- Two-stage chat pipeline: retrieval → final answer generation with tool-first assistant architecture.
- **Deep research** streaming via `POST /api/v2/chat/agent/stream` (orchestration graph, SSE).
- Contract analysis improvements: effective-query handling, risk analysis for attachments, DOCX final-answer uploads.
- Criminal court assistant: Neo4j + MongoDB case retrieval tools, criminal mode classification, improved metadata filter sanitization.
- Administrative court routing and prompt registry integration.
- Optional **Langfuse** tracing (user/session/tags, final-answer input recording, flush on shutdown).
- Optional orchestration debug: LLM context JSON export, thread-state logging (later trimmed in refactors).
- Current-date context block in the final system prompt.

#### Legal projects (workspaces) — new in v3

Project workspaces let users group case documents, chat sessions, and custom agent instructions in one place. RAG over project files is isolated by `project_id` + `user_id` in Milvus (`project_files` collection).

**REST API** — base path `/api/v2/history/projects` (see [docs/file-management.md](docs/file-management.md)):

| Method | Path | Description |
| --- | --- | --- |
| `GET` | `/user/{user_id}` | List projects for a user (optional `status`, pagination) |
| `POST` | `/` | Create project (`owner_id`, `title`, `settings`, `metadata`) |
| `GET` | `/{project_id}?owner_id=` | Get project |
| `PATCH` | `/{project_id}` | Update title, status (`active` / `archived` / `closed`), settings |
| `GET` | `/{project_id}/instructions?user_id=` | Read project instructions for agents |
| `POST` | `/{project_id}/instructions` | Create instructions (once) |
| `PUT` | `/{project_id}/instructions` | Replace instructions |
| `POST` | `/{project_id}/sessions` | Create chat session linked to project |
| `GET` | `/{project_id}/sessions?user_id=` | List sessions in project |
| `POST` | `/{project_id}/files` | Upload file (multipart: `file`, `user_id`, optional `session_id`, `webhook_url`) |
| `GET` | `/{project_id}/files?user_id=` | List files in project |
| `DELETE` | `/{project_id}/files/{file_id}?user_id=` | Delete project file (Mongo + vectors) |
| `POST` | `/{project_id}/search` | Hybrid search over project file embeddings (`q`, `top_k`) |

**Chat integration**

- `POST /api/v2/chat/ask` and `POST /api/v3/chat/ask` accept optional **`project_id`** on the request body.
- When set, orchestration loads project instructions and retrieves context from **`project_files`** in Milvus (not the global legal corpus only).
- Sessions can be attached to a project via `attach_session_to_project`; listing user sessions can exclude project-bound sessions where applicable.

**Storage & pipeline**

- MongoDB `projects` collection for workspace metadata, stats, and settings.
- Uploaded files indexed into Milvus with `metadata.project_id` and `metadata.user_id`.
- Oversized files: lightweight DOCX path, chunking/truncation for embeddings (see file pipeline commits).

**Related commits (Apr–May 2026):** `37a26a0`, `cac0e2b`, `1cb4093`, `14e217e`, `270e5a8`, `c9e2ff0`.

#### API routes & integrations
- **Promo codes** moved to dedicated router: `/api/v2/promo-codes/*` (create, activate/deactivate, assign, user lookup).
- **Rate limit / credits**: `GET /api/v2/history/users/rate-limit/{user_id}` (replaces admin path).
- **Referral tracking**: `POST /api/v2/referrals/track`, stats endpoint.
- **Bitrix24 CRM**: automatic lead creation when users register or add a phone number (`bitrix24_lead_id` on user docs). See [docs/bitrix24-leads.md](docs/bitrix24-leads.md).
- Public file metadata and signed download/view URLs for uploaded files.
- Enhanced file pipeline: oversized-file indexing, lightweight DOCX processing, higher token limits with truncation, Datalab-first OCR.

#### Documentation
- Central docs index, full [api-reference.md](docs/api-reference.md), [updates-v2.md](docs/updates-v2.md) migration guide.

### Changed

- **CrewAI → LangGraph** for agentic RAG and assistant routing.
- **`agents/` → `assistants/`** package layout; consolidated LLM provider modules.
- Default lite model updated to **gpt-4.1-mini**; classifier models refactored; Gemini default `gemini-3.1-pro-preview`.
- `DEVELOPMENT_MODE` removed in favor of **`DEBUG`** for environment behavior.
- Session listing filters active, non-project sessions where applicable.
- Streaming responses: consistent end events; attachment handling streamlined in chat service.
- Docker: `compose.yml` rename, resource limits for API/Redis, dependency pins (`langchain-neo4j`, `tenacity`, MongoDB LangGraph packages).
- Auth service import cleanup and validation helpers.

### Removed

- `super_secret_admin_key` from JSON bodies and query strings (use headers instead). See [docs/updates-v2.md](docs/updates-v2.md).
- **`GET /api/v2/admin/rate-limit/{user_id}`** — use history users path above.
- Promo code CRUD from `/api/v2/admin/*` — use `/api/v2/promo-codes/*`.
- Obsolete test suite and deprecated maintenance scripts.
- Milvus service from local Docker Compose (external/cluster Milvus assumed).

### Fixed

- Hybrid search filter handling and logging on Milvus failures.
- UTC timestamps in chat history; file content hashing in file manager.
- Docker Compose and image build refinements.
- Telegram login OIDC experiment **reverted** (2026-05-20); prior Telegram auth behavior restored.

### Security & authentication

| Header | Purpose |
| --- | --- |
| `admin` | Standard API key (`API_KEY`) |
| `x-dt-team-api-key` | DT team backend |
| `x-super-admin-key` | User create/block, Telegram admin (`/api/v2/admin/telegram/*`) |

Telegram save/list endpoints require **`x-super-admin-key`** only (not the regular `admin` key).

### Breaking changes (migrate before upgrading clients)

1. **Credits / rate limit**  
   `GET /api/v2/admin/rate-limit/{user_id}` → `GET /api/v2/history/users/rate-limit/{user_id}`  
   Auth: `admin` or `x-dt-team-api-key`.

2. **Promo codes**  
   `/api/v2/admin/promo-codes/...` → `/api/v2/promo-codes/...`  
   Remove `super_secret_admin_key` from create payloads; use `admin` header.

3. **Telegram admin**  
   Remove `super_secret_admin_key` from body/query; send `x-super-admin-key` header.

4. **Chat (recommended)**  
   New integrations may call `POST /api/v3/chat/ask`; existing v2 chat paths still work.

Full checklist: [docs/updates-v2.md](docs/updates-v2.md).

### Upgrade notes

- Set `BITRIX24_WEBHOOK_URL` (and related vars) to enable CRM leads.
- Set `LANGFUSE_*` vars only if tracing is desired.
- Redis URI required for LangGraph checkpoints in production.
- Neo4j connection required for criminal-case graph retrieval.
- Milvus collection **`project_files`** (or `MILVUS_PROJECT_FILES` in env) required for project-scoped document search.

---

## [2.3.1] — 2026-03-24

- Subscription storage initialization fix in rate limit service.

## [2.3.0] and earlier

See git history and tags `v2.0.0` … `v2.3.0` on the repository.
