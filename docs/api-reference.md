# WakilAI API Reference

This document provides the **complete list of HTTP and WebSocket endpoints** for the WakilAI API.
For feature-specific flows, see the docs index in [docs/README.md](README.md).

## Base URLs

- **v2 API base**: `/api/v2`
- **v3 API base**: `/api/v3`

## Authentication & Access

- **Default API key**: sent via the header configured as `API_KEY_NAME` (default: `x-api-key`).
- **DT team API key**: sent via `DT_API_KEY_NAME` (default: `x-dt-team-api-key`).
- **Super admin key**: header `x-super-admin-key` for sensitive ops (user create/block, Telegram admin). See [updates-v2.md](updates-v2.md) for recent route moves.
- **Docs protection**: `/docs`, `/redoc`, `/swagger-ui.html`, and `/openapi.json` require HTTP Basic Auth (`DOCS_USER` / `DOCS_PASSWORD`).

**Public endpoints (no API key required):**
- `GET /`
- `GET /health`
- `POST /api/v2/referrals/track`
- `GET /api/v2/share/{share_id}`

## Health & Documentation

| Method | Path | Description |
| --- | --- | --- |
| GET | `/` | Health check (alias) |
| GET | `/health` | Health check |
| GET | `/docs` | Swagger UI (Basic Auth) |
| GET | `/redoc` | ReDoc (Basic Auth) |
| GET | `/swagger-ui.html` | Swagger UI (Basic Auth) |
| GET | `/openapi.json` | OpenAPI schema (Basic Auth) |

## Auth (v2)

Base path: `/api/v2/auth`

| Method | Path | Description |
| --- | --- | --- |
| GET | `/api/v2/auth/google/login` | Start Google OAuth login |
| GET | `/api/v2/auth/google/callback` | Google OAuth callback |
| GET | `/api/v2/auth/telegram/login` | Telegram login validation |
| POST | `/api/v2/auth/dt` | Create DT user (DT API key required) |

## Chat (v2)

Base path: `/api/v2/chat`

| Method | Path | Description |
| --- | --- | --- |
| POST | `/api/v2/chat/ask` | Ask a legal question (main + specialized assistants) |
| POST | `/api/v2/chat/agent/stream` | Streaming agentic RAG flow (SSE) |
| GET | `/api/v2/chat/assistants` | List available assistants |
| GET | `/api/v2/chat/model-info` | LLM & embedding configuration |

## Chat History (v2)

Base path: `/api/v2/history`

### Users

| Method | Path | Description |
| --- | --- | --- |
| POST | `/api/v2/history/users` | Create or return user (super admin key) |
| GET | `/api/v2/history/users/{user_id}` | Get user profile |
| PATCH | `/api/v2/history/users/phone-number` | Update user phone number |
| PATCH | `/api/v2/history/users/change/info/{user_id}` | Update a single user field |
| PATCH | `/api/v2/history/users/block/{user_id}` | Block a user (super admin key) |
| PATCH | `/api/v2/history/users/unblock/{user_id}` | Unblock a user (super admin key) |
| GET | `/api/v2/history/users/rate-limit/{user_id}` | Get user credit / rate-limit status |

### Sessions

| Method | Path | Description |
| --- | --- | --- |
| POST | `/api/v2/history/sessions` | Create a chat session |
| GET | `/api/v2/history/sessions/{user_id}` | List sessions for a user |
| GET | `/api/v2/history/sessions/{user_id}/{session_id}` | Get a specific session |
| PATCH | `/api/v2/history/sessions/{session_id}` | Update session title/tags |
| DELETE | `/api/v2/history/sessions/{session_id}` | Delete a session |

### Messages

| Method | Path | Description |
| --- | --- | --- |
| POST | `/api/v2/history/messages` | Create a message |
| GET | `/api/v2/history/messages/{session_id}` | List messages in a session |
| GET | `/api/v2/history/messages/{session_id}/{message_id}` | Get a message |
| POST | `/api/v2/history/messages/{message_id}/share` | Create a share link for a message |

### Feedback

| Method | Path | Description |
| --- | --- | --- |
| POST | `/api/v2/history/feedback` | Submit feedback |
| GET | `/api/v2/history/feedback/{message_id}` | Get feedback |
| DELETE | `/api/v2/history/feedback/{message_id}` | Delete feedback |

### Files

| Method | Path | Description |
| --- | --- | --- |
| GET | `/api/v2/history/files/user/{user_id}` | List uploaded files for a user |
| POST | `/api/v2/history/files` | Upload file (multipart/form-data) |
| GET | `/api/v2/history/files/{file_id}/public-metadata` | Public file metadata (name/type/size) |
| PATCH | `/api/v2/history/files/{file_id}/message` | Link file to a message |
| GET | `/api/v2/history/files/{file_id}` | Get full file record |
| GET | `/api/v2/history/files/{file_id}/view` | Get a signed view URL |
| GET | `/api/v2/history/files/{file_id}/download` | Redirect to signed download URL |
| DELETE | `/api/v2/history/files/{file_id}` | Delete file |

### Shares (Public)

| Method | Path | Description |
| --- | --- | --- |
| GET | `/api/v2/share/{share_id}` | Fetch shared message by ID |

### Projects (legal workspaces)

Base path: `/api/v2/history/projects` — project-scoped documents, sessions, and RAG. See [file-management.md](file-management.md).

| Method | Path | Description |
| --- | --- | --- |
| GET | `/api/v2/history/projects/user/{user_id}` | List owned and shared projects (`membership_role`, `status`, `limit`, `skip`) |
| POST | `/api/v2/history/projects` | Create project |
| GET | `/api/v2/history/projects/{project_id}` | Get project (`owner_id` query = caller user id) |
| PATCH | `/api/v2/history/projects/{project_id}` | Update project (**owner only**) |
| GET | `/api/v2/history/projects/{project_id}/instructions` | Get project instructions (`user_id` query) |
| POST | `/api/v2/history/projects/{project_id}/instructions` | Create project instructions |
| PUT | `/api/v2/history/projects/{project_id}/instructions` | Update project instructions |
| POST | `/api/v2/history/projects/{project_id}/sessions` | Create session in project |
| GET | `/api/v2/history/projects/{project_id}/sessions` | List project sessions (`user_id`, pagination) |
| POST | `/api/v2/history/projects/{project_id}/files` | Upload file to project (multipart) |
| GET | `/api/v2/history/projects/{project_id}/files` | List project files (`user_id` query) |
| DELETE | `/api/v2/history/projects/{project_id}/files/{file_id}` | Delete project file (`user_id` query) |
| POST | `/api/v2/history/projects/{project_id}/search` | Hybrid search all project file vectors (`user_id` must have access) |

### Project collaboration (invites & members)

**Frontend integration guide:** [frontend-project-collaboration.md](frontend-project-collaboration.md) (flows, types, `invite_id` vs `id`, URL building, checklist).

Base path: `/api/v2/history/projects` — owner manages invites/members; members get full project access except PATCH project and member admin.

| Method | Path | Description |
| --- | --- | --- |
| GET | `/api/v2/history/projects/invites/{invite_id}` | Preview invite (`user_id` query) |
| POST | `/api/v2/history/projects/invites/{invite_id}/accept` | Accept invite (`user_id` in body) |
| GET | `/api/v2/history/projects/{project_id}/members` | List owner and members (`user_id` query) |
| DELETE | `/api/v2/history/projects/{project_id}/members/{member_user_id}` | Remove member (**owner only**, `user_id` query) |
| POST | `/api/v2/history/projects/{project_id}/invites?user_id=` | Create invite link (**owner only** — pass logged-in owner’s id as query param, not `project.owner_id` from a member’s UI) |
| GET | `/api/v2/history/projects/{project_id}/invites` | List pending invites (**owner only**, `user_id` query) |
| DELETE | `/api/v2/history/projects/{project_id}/invites/{invite_id}` | Revoke invite (**owner only**, `user_id` query) |

Invite links use token id `pinv-...`; frontend opens `projects/join/{invite_id}` and calls the accept endpoint. Each invite is single-use. Collaborators use their own credits for chat.

Chat: pass optional `project_id` on `POST /api/v2/chat/ask` or `POST /api/v3/chat/ask` to scope retrieval and agent context to that project.

## Memory (v2)

Base path: `/api/v2/memory`

| Method | Path | Description |
| --- | --- | --- |
| GET | `/api/v2/memory/user/{user_id}/` | Get all memories for a user |
| DELETE | `/api/v2/memory/user/{user_id}/` | Delete all memories for a user |
| POST | `/api/v2/memory/save/` | Save interaction to memory |
| DELETE | `/api/v2/memory/{memory_id}/` | Delete a memory by ID |
| PUT | `/api/v2/memory/update/{memory_id}` | Update a memory by ID |

## Speech-to-Text (v2)

Base path: `/api/v2/speech-to-text`

| Method | Path | Description |
| --- | --- | --- |
| POST | `/api/v2/speech-to-text/transcribe` | Transcribe audio file |
| WS | `/api/v2/speech-to-text/ws/transcribe` | Streaming WebSocket transcription |

## Promo codes (v2)

Base path: `/api/v2/promo-codes`

| Method | Path | Description |
| --- | --- | --- |
| POST | `/api/v2/promo-codes` | Create promo code (`admin` header) |
| GET | `/api/v2/promo-codes/{code}` | Get promo code (`admin` header) |
| PATCH | `/api/v2/promo-codes/{code}/activate` | Activate promo code (`admin` header) |
| PATCH | `/api/v2/promo-codes/{code}/deactivate` | Deactivate promo code (`admin` header) |
| POST | `/api/v2/promo-codes/assign` | Assign promo code to user |
| DELETE | `/api/v2/promo-codes/assign/{user_id}` | Remove promo code from user |
| GET | `/api/v2/promo-codes/users/{user_id}` | Get user's promo code assignment |

Assign / user lookup endpoints accept `admin` or `x-dt-team-api-key`. See [updates-v2.md](updates-v2.md).

## Admin (v2)

Base path: `/api/v2/admin` — requires `x-super-admin-key` (internal ops).

| Method | Path | Description |
| --- | --- | --- |
| POST | `/api/v2/admin/telegram/save/chats` | Upsert Telegram chat IDs |
| GET | `/api/v2/admin/telegram/chats` | List Telegram chats |

## Payments & Subscriptions (v2)

Base path: `/api/v2/transaction`

| Method | Path | Description |
| --- | --- | --- |
| POST | `/api/v2/transaction/payme/` | Payme RPC endpoint |
| POST | `/api/v2/transaction/payme/callback` | Create Payme payment link |
| POST | `/api/v2/transaction/payme/init` | Init Payme checkout |
| POST | `/api/v2/transaction/click/init` | Init Click checkout |
| POST | `/api/v2/transaction/click/prepare` | Click prepare callback |
| POST | `/api/v2/transaction/click/complete` | Click complete callback |
| GET | `/api/v2/transaction/payme/subscriptions/catalog` | Subscription catalog |
| GET | `/api/v2/transaction/payme/subscriptions/{user_id}` | User subscription status |
| POST | `/api/v2/transaction/dt/init` | DT subscription apply (DT API key) |

## Referrals (v2)

Base path: `/api/v2/referrals`

| Method | Path | Description |
| --- | --- | --- |
| POST | `/api/v2/referrals/track?from=source` | Track referral source (public) |
| GET | `/api/v2/referrals/stats?limit=100` | List referral stats (API key) |

## Chat (v3)

Base path: `/api/v3/chat`

| Method | Path | Description |
| --- | --- | --- |
| POST | `/api/v3/chat/ask` | Backward-compatible chat endpoint proxied to `rest-api-llm` |
