# WakilAI API Reference

This document provides the **complete list of HTTP and WebSocket endpoints** for the WakilAI API.
For feature-specific flows, see the docs index in [docs/README.md](README.md).

## Base URLs

- **v2 API base**: `/api/v2`
- **v3 API base**: `/api/v3`

## Authentication & Access

- **Default API key**: sent via the header configured as `API_KEY_NAME` (default: `x-api-key`).
- **DT team API key**: sent via `DT_API_KEY_NAME` (default: `x-dt-team-api-key`).
- **Admin-only key**: some endpoints require `super_secret_admin_key` in the JSON body.
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

## Admin (v2)

Base path: `/api/v2/admin`

| Method | Path | Description |
| --- | --- | --- |
| GET | `/api/v2/admin/rate-limit/{user_id}` | Get user credit status |
| POST | `/api/v2/admin/promo-codes` | Create promo code (admin key + body secret) |
| GET | `/api/v2/admin/promo-codes/{code}` | Get promo code |
| PATCH | `/api/v2/admin/promo-codes/{code}/activate` | Activate promo code |
| PATCH | `/api/v2/admin/promo-codes/{code}/deactivate` | Deactivate promo code |
| POST | `/api/v2/admin/promo-codes/assign` | Assign promo code to user |
| DELETE | `/api/v2/admin/promo-codes/assign/{user_id}` | Remove promo code from user |
| GET | `/api/v2/admin/promo-codes/users/{user_id}` | Get user's promo code assignment |
| POST | `/api/v2/admin/telegram/save/chats` | Upsert Telegram chat IDs (admin key + body secret) |
| GET | `/api/v2/admin/telegram/chats` | List Telegram chats (admin key + query secret) |

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
| POST | `/api/v3/chat/ask` | LangChain-only general assistant (`main`) |
