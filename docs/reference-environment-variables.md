# Environment Variables Reference

Source of truth: [`src/core/config.py`](../src/core/config.py).

`rest-api` should not require OpenAI, Gemini, Anthropic, Milvus, Pinecone, Neo4j, Mem0, OCR, or STT provider keys. Configure those in `rest-api-llm`.

## App

| Variable | Default | Description |
| --- | --- | --- |
| `APP_NAME` | `WakilAI Chatbot` | API title |
| `API_PREFIX` | `/api/v2` | Legacy public API prefix |
| `VERSION` | `3.0.0` | API version metadata |
| `DEBUG` | `false` | Enables permissive CORS in development |
| `HOST_URL` | `https://backend.wakil.ai` | Public backend URL |
| `ALLOWED_ORIGINS` | chat/dev domains | CORS allowlist |

## Internal LLM Service

| Variable | Default | Description |
| --- | --- | --- |
| `LLM_SERVICE_URL` | `null` | Base URL for `rest-api-llm` |
| `LLM_SERVICE_INTERNAL_TOKEN` | `null` | Internal auth token sent to `rest-api-llm` |
| `LLM_SERVICE_INTERNAL_HEADER` | `x-internal-token` | Internal auth header name |
| `LLM_SERVICE_TIMEOUT_SECONDS` | `600` | HTTP timeout for internal calls |
| `LLM_SERVICE_EMBEDDING_MODEL_NAME` | `rest-api-llm` | Public metadata label |

## MongoDB

| Variable | Default | Description |
| --- | --- | --- |
| `MONGODB_URI` | `null` | MongoDB connection URI |
| `MONGODB_DB_NAME` | `wakilai` | Main database |
| `COLLECTION_NAME` | `null` | Legacy collection name |
| `CRIMINAL_CASES_MONGODB_DATABASE` | `criminal` | Criminal excerpt bridge database |
| `USERS_COLLECTION` | `users` | Users |
| `SESSIONS_COLLECTION` | `sessions` | Chat sessions |
| `MESSAGES_COLLECTION` | `messages` | Messages |
| `FILES_COLLECTION` | `files` | Uploaded files |
| `PROJECTS_COLLECTION` | `projects` | Projects |
| `PROJECT_MEMBERS_COLLECTION` | `project_members` | Project members |
| `PROJECT_INVITES_COLLECTION` | `project_invites` | Project invites |
| `PROMO_CODE_COLLECTION` | `promos` | Promo codes |
| `USER_PROMO_CODE_COLLECTION` | `user-promos` | User promo usage |
| `TRANSACTION_COLLECTION` | `transactions` | Transactions |
| `SUBSCRIPTIONS_COLLECTION` | `subscriptions` | Subscriptions |
| `DAILY_SUBSCRIPTIONS_COLLECTION` | `daily_subscriptions` | Daily subscriptions |
| `RATE_LIMIT_COLLECTION` | `creditusage` | Credit usage |
| `TOKEN_COUNTING_COLLECTION` | `token_counts` | Token count records |
| `TELEGRAM_CHATS_COLLECTION` | `telegram_chats` | Telegram chat links |
| `REFERRAL_SOURCES_COLLECTION` | `referral_sources` | Referral sources |
| `FINGERPRINTS_COLLECTION` | `fingerprints` | Device fingerprints |

## File And Chat Behavior

| Variable | Default | Description |
| --- | --- | --- |
| `STREAM` | `true` | Default streaming behavior |
| `STREAM_KEEPALIVE_INTERVAL_SECONDS` | `60` | SSE heartbeat interval |
| `STREAM_SSE_MAX_RESPONSE_CHARS` | `200` | SSE chunk size cap |
| `TOP_K` | `10` | Search top-k forwarded to `rest-api-llm` |
| `FILE_SEARCH_TOP_K` | `3` | File search top-k forwarded to `rest-api-llm` |
| `FILE_CONTENT_TOKEN_LIMIT` | `50000` | Inline uploaded-file context limit |
| `CHAT_HISTORY_LIMIT` | `3` | Stored history read limit |
| `MAX_QUERY_LENGTH` | `5000` | Public query validation limit |
| `PROJECT_FILES_INDEX_NAME` | `project_files` | Compatibility metadata label |
| `CONTRACT_ATTACHMENT_MIN_SIMILARITY` | `0.6` | Contract attachment threshold metadata |

## Auth And Security

| Variable | Default | Description |
| --- | --- | --- |
| `API_KEY_NAME` | `admin` | Public API key header |
| `API_KEY` | `admin` | Public API key value |
| `SUPER_ADMIN_KEY_NAME` | `x-super-admin-key` | Super-admin key header |
| `SUPER_ADMIN_API_KEY` | `super-admin` | Super-admin key value |
| `DT_API_KEY_NAME` | `x-dt-team-api-key` | DT team API key header |
| `DT_API_KEY` | `null` | DT team API key |
| `DT_TEAM_DISCLAIMER` | empty | Optional DT response disclaimer |
| `DOCS_USER` | `admin` | Docs basic-auth username |
| `DOCS_PASSWORD` | `admin` | Docs basic-auth password |
| `AUTH_SECRET_KEY` | `secret-key-change-me` | Session cookie signing secret |
| `JWT_SECRET_KEY` | no default | JWT signing secret; must be at least 32 characters |
| `JWT_ALGORITHM` | `HS256` | JWT signing algorithm (`HS256`, `HS384`, or `HS512`) |
| `JWT_ISSUER` | `wakilai-frontend` | Required issuer for frontend-signed JWTs |
| `JWT_AUDIENCE` | `wakilai-rest-api` | Required backend audience claim |
| `AUTH_USER_STATUS_CACHE_TTL_SECONDS` | `60` | Redis TTL for cached user authentication status |

## Login And OTP

| Variable | Default | Description |
| --- | --- | --- |
| `GOOGLE_CLIENT_ID` | `null` | Google OAuth client ID |
| `GOOGLE_CLIENT_SECRET` | `null` | Google OAuth client secret |
| `GOOGLE_REDIRECT_URI` | `null` | Google OAuth callback |
| `TELEGRAM_BOT_TOKEN` | `null` | Telegram login bot token |
| `TELEGRAM_BOT_LOGIN` | `null` | Telegram bot username |
| `TELEGRAM_SESSION_TIMEOUT` | `259200` | Telegram login max age |
| `TWILIO_ACCOUNT_SID` | `null` | Twilio SID |
| `TWILIO_AUTH_TOKEN` | `null` | Twilio auth token |
| `TWILIO_VERIFY_SERVICE_SID` | `null` | Twilio Verify service |
| `OTP_SEND_LIMIT_PER_PHONE` | `3` | OTP send limit |
| `OTP_SEND_WINDOW_SECONDS` | `3600` | OTP send window |
| `OTP_VERIFY_LIMIT_PER_PHONE` | `10` | OTP verify limit |
| `OTP_VERIFY_WINDOW_SECONDS` | `3600` | OTP verify window |

## Google Cloud Server Identity

| Variable | Default | Description |
| --- | --- | --- |
| `GOOGLE_PROJECT_ID` | `null` | Google Cloud project ID |
| `GOOGLE_CLIENT_EMAIL` | `null` | Server service-account email |
| `GOOGLE_PRIVATE_KEY_ID` | `null` | Server service-account private-key ID |
| `GOOGLE_PRIVATE_KEY` | `null` | PEM private key; literal `\\n` sequences are supported |
| `GOOGLE_SERVICE_ACCOUNT_CLIENT_ID` | `null` | Server service-account client ID |
| `GOOGLE_APPLICATION_CREDENTIALS` | `null` | Optional local JSON path fallback |

## Storage

| Variable | Default | Description |
| --- | --- | --- |
| `GCS_BUCKET_NAME` | `null` | GCS bucket used by this server |

## Redis

| Variable | Default | Description |
| --- | --- | --- |
| `REDIS_HOST` | `localhost` | Redis host |
| `REDIS_PORT` | `6379` | Redis port |
| `REDIS_PASSWORD` | `null` | Redis password |
| `REDIS_EXPIRATION_SECONDS` | `259200` | Default cache TTL |

## Payments And CRM

Payment and CRM variables are still owned by `rest-api`; see `.env.example` for the full provider list.
