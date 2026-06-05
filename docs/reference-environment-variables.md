# Environment Variables Reference

Complete listing of every environment variable the WakilAI REST API reads from `.env` (or the process environment). Source of truth: [src/core/config.py](../src/core/config.py).

Values shown are the code defaults — override any of them in `.env`.

---

## App / General

| Variable | Type | Default | Description |
|----------|------|---------|-------------|
| `APP_NAME` | str | `WakilAI Chatbot` | Application name (appears in logs and API metadata) |
| `API_PREFIX` | str | `/api/v2` | Legacy prefix; v3 routes are hardcoded at `/api/v3` |
| `VERSION` | str | `3.0.0` | API version string returned in metadata |
| `DEBUG` | bool | `false` | Enables hot-reload-friendly CORS (allows all origins) and debug-level logs |
| `HOST_URL` | str | `https://backend.wakil.ai` | Public URL of this service; used for constructing signed GCS URLs |
| `ALLOWED_ORIGINS` | list | `["https://chat.wakil.ai", ...]` | CORS allowed origins (ignored when `DEBUG=true`) |

---

## Auth & Security

### API Key Authentication

| Variable | Type | Default | Description |
|----------|------|---------|-------------|
| `API_KEY_NAME` | str | `admin` | HTTP header name that carries the regular API key |
| `API_KEY` | str | `admin` | Value of the regular API key. **Change in production.** |
| `SUPER_ADMIN_KEY_NAME` | str | `x-super-admin-key` | Header name for super-admin operations |
| `SUPER_ADMIN_API_KEY` | str | `super-admin` | Super-admin key value. **Change in production.** |
| `DT_API_KEY_NAME` | str | `x-dt-team-api-key` | Header name for DT/Birdarcha backend access |
| `DT_API_KEY` | str\|None | `null` | DT team API key value |
| `DT_TEAM_DISCLAIMER` | str | `""` | Optional disclaimer text appended to DT team responses |

### Documentation Access

| Variable | Type | Default | Description |
|----------|------|---------|-------------|
| `DOCS_USER` | str | `admin` | HTTP Basic Auth username for `/docs`, `/redoc`, `/openapi.json` |
| `DOCS_PASSWORD` | str | `admin` | HTTP Basic Auth password for API docs. **Change in production.** |

### Google OAuth

| Variable | Type | Default | Description |
|----------|------|---------|-------------|
| `GOOGLE_CLIENT_ID` | str\|None | `null` | Google OAuth 2.0 client ID |
| `GOOGLE_CLIENT_SECRET` | str\|None | `null` | Google OAuth 2.0 client secret |
| `GOOGLE_REDIRECT_URI` | str\|None | `null` | OAuth callback URL (e.g. `https://backend.wakil.ai/api/v2/auth/google/callback`) |
| `AUTH_SECRET_KEY` | str | `secret-key-change-me` | Signs session cookies for OAuth state. **Change in production.** |

### Telegram

| Variable | Type | Default | Description |
|----------|------|---------|-------------|
| `TELEGRAM_BOT_TOKEN` | str\|None | `null` | Telegram bot token for login widget HMAC validation |
| `TELEGRAM_BOT_LOGIN` | str\|None | `null` | Telegram bot username (e.g. `wakilai_bot`) |
| `TELEGRAM_SESSION_TIMEOUT` | int | `259200` | Max age (seconds) of a valid Telegram login widget payload (default: 3 days) |

### DT/OneID Integration

| Variable | Type | Default | Description |
|----------|------|---------|-------------|
| `DT_SERVER_IP` | str | `87.192.230.47` | OneID server IP for Birdarcha web client |
| `DT_WEB_CLIENT_NAME` | str | `birdarcha` | Web client name registered with OneID |
| `WAKILAI_WEB_CLIENT_NAME` | str | `wakilai` | WakilAI's own client name with OneID |

---

## Database — MongoDB

| Variable | Type | Default | Description |
|----------|------|---------|-------------|
| `MONGODB_URI` | str\|None | `null` | Motor async connection URI (e.g. `mongodb://localhost:27017`) |
| `MONGODB_DB_NAME` | str | `wakilai` | Primary database name |
| `COLLECTION_NAME` | str\|None | `null` | Main legal-docs collection name (legacy; most collections are per-assistant) |
| `CRIMINAL_CASES_MONGODB_DATABASE` | str | `criminal` | Separate database for criminal case documents |

### MongoDB Collection Names

These let you rename collections without code changes.

| Variable | Default |
|----------|---------|
| `USERS_COLLECTION` | `users` |
| `SESSIONS_COLLECTION` | `sessions` |
| `MESSAGES_COLLECTION` | `messages` |
| `FILES_COLLECTION` | `files` |
| `PROJECTS_COLLECTION` | `projects` |
| `PROMO_CODE_COLLECTION` | `promos` |
| `USER_PROMO_CODE_COLLECTION` | `user-promos` |
| `TRANSACTION_COLLECTION` | `transactions` |
| `SUBSCRIPTIONS_COLLECTION` | `subscriptions` |
| `DAILY_SUBSCRIPTIONS_COLLECTION` | `daily_subscriptions` |
| `RATE_LIMIT_COLLECTION` | `creditusage` |
| `TOKEN_COUNTING_COLLECTION` | `token_counts` |
| `TELEGRAM_CHATS_COLLECTION` | `telegram_chats` |
| `REFERRAL_SOURCES_COLLECTION` | `referral_sources` |
| `PAYMENT_INVOICES_COLLECTION` | `payment_invoices` |
| `PAYME_INVOICES_COLLECTION` | `payme_invoices` |
| `PAYME_FISCAL_COLLECTION` | `payme_fiscal` |
| `CLICK_INVOICES_COLLECTION` | `click_invoices` |
| `CLICK_TRANSACTIONS_COLLECTION` | `click_transactions` |

---

## Database — Neo4j (Criminal Cases)

| Variable | Type | Default | Description |
|----------|------|---------|-------------|
| `NEO4J_URI` | str\|None | `null` | Neo4j bolt URI (e.g. `bolt://localhost:7687`). Leave unset to disable. |
| `NEO4J_USERNAME` | str | `neo4j` | Neo4j username |
| `NEO4J_PASSWORD` | str\|None | `null` | Neo4j password |
| `NEO4J_DATABASE` | str | `neo4j` | Neo4j database name |
| `NEO4J_CASE_VECTOR_INDEX` | str | `case_summary_embedding` | Vector index name on Case nodes |
| `NEO4J_CASE_FULLTEXT_INDEX` | str | `""` | Full-text index for hybrid (keyword + vector) search; leave empty for vector-only |
| `NEO4J_CASE_VECTOR_RETRIEVAL_QUERY` | str | (coalesce query) | Cypher suffix for `Neo4jVector` retrieval |
| `NEO4J_CASE_FILTER_ARTICLE_PROP` | str\|None | `null` | Case node property for article-number metadata filtering |
| `NEO4J_CASE_FILTER_COURT_PROP` | str\|None | `null` | Case node property for court-type metadata filtering |
| `NEO4J_CASE_FILTER_INSTANCE_PROP` | str\|None | `null` | Case node property for court-instance metadata filtering |
| `NEO4J_SECTION_VECTOR_INDEX` | str | `legal_section_embedding` | Vector index on legal section nodes |
| `CRIMINAL_GRAPH_RETRIEVAL_ENABLED` | bool | `true` | Set to `false` to skip Neo4j subgraph and use Milvus-only for criminal queries |

---

## Database — Redis

| Variable | Type | Default | Description |
|----------|------|---------|-------------|
| `REDIS_HOST` | str | `localhost` | Redis host |
| `REDIS_PORT` | int | `6379` | Redis port |
| `REDIS_PASSWORD` | str\|None | `null` | Redis AUTH password |
| `REDIS_URI` | str\|None | `null` | Full Redis URL (overrides host/port if set). Format: `redis://:password@host:port/0` |
| `REDIS_EXPIRATION_SECONDS` | int | `259200` | General Redis cache TTL (3 days) |
| `LANGGRAPH_CHECKPOINT_USE_REDIS` | bool | `true` | Persist LangGraph thread state in Redis. Requires Redis Stack (RediSearch + RedisJSON). |
| `LANGGRAPH_CHECKPOINT_TTL_SECONDS` | int | `259200` | TTL for LangGraph checkpoint keys (3 days) |

> **Redis Stack is required.** Plain Redis lacks the `FT.*` commands used by `AsyncRedisSaver`. Run `redis/redis-stack-server:7.4.0-v3` or later.

---

## Vector Database — Milvus

| Variable | Type | Default | Description |
|----------|------|---------|-------------|
| `VECTOR_DB_TYPE` | enum | `milvus` | `milvus` or `pinecone` |
| `MILVUS_URI` | str | `http://localhost:19530` | Milvus gRPC/HTTP URI |
| `MILVUS_USER` | str\|None | `null` | Milvus username (for RBAC-enabled deployments) |
| `MILVUS_PASSWORD` | str\|None | `null` | Milvus password |
| `MILVUS_TOKEN` | str\|None | `null` | Milvus API token (takes precedence over user/password) |

### Milvus Collection Names

| Variable | Default | Assistant |
|----------|---------|-----------|
| `MILVUS_MAIN_NAME` | `lexuz` | `main` — general legal documents |
| `MILVUS_TAX_COLLECTION` | `soliq` | `tax` — tax law corpus |
| `MILVUS_PROJECT_FILES` | `project_files` | User-uploaded files for legal projects |
| `MILVUS_ADMINISTRATIVE_COURT` | `mamuriy_sud` | `administrative_court` (filtered subset) |
| `MILVUS_ADMINISTRATIVE_COURT_ALL` | `mamuriy_sud_all` | `court` router — all administrative docs |
| `MILVUS_CONTRACT_ANALYZER` | `shartnoma` | `contract_analyzer` — contract templates |
| `MILVUS_ECONOMIC_COURT` | `economic_court` | `economic_court` specialist |
| `MILVUS_CIVIL_COURT` | `civil_court` | `civil_court` specialist |
| `MILVUS_CRIMINAL_COURT` | `criminal_court` | `criminal_court` (supplementary Milvus after Neo4j) |
| `CONTRACT_ATTACHMENT_MIN_SIMILARITY` | `0.6` | Min hybrid-search score to attach a contract DOCX |

---

## Vector Database — Pinecone (fallback)

| Variable | Type | Default | Description |
|----------|------|---------|-------------|
| `PINECONE_API_KEY` | str\|None | `null` | Pinecone API key |
| `PINECONE_ENVIRONMENT` | str\|None | `null` | Pinecone environment region |
| `PINECONE_INDEX_NAME` | str\|None | `null` | Pinecone index name |
| `NAMESPACE_NAME` | str | `lexuz` | Pinecone namespace for legal documents |

---

## LLM Providers

### Model Selection

| Variable | Type | Default | Description |
|----------|------|---------|-------------|
| `LLM_PROVIDER` | enum | `openai` | Default provider for lite tasks: `openai` or `novita` |
| `DEFAULT_CHAT_MODEL` | str | `gemini-3.1-pro-preview` | Generation model for final answers |
| `DEFAULT_LITE_MODEL` | str | `gpt-4.1-mini` | Fast/cheap model for routing, rewrite, intent, Milvus filter generation |
| `GPT_COMPLETION_MODEL` | str | `gpt-5.2` | Legacy OpenAI fallback model |

### Generation Parameters

| Variable | Type | Default | Description |
|----------|------|---------|-------------|
| `TEMPERATURE` | float | `0.1` | LLM generation temperature (lower = more deterministic) |
| `OUTPUT_MAX_TOKENS` | int | `8192` | Max tokens in the final answer |
| `CHAT_HISTORY_LIMIT` | int | `3` | Number of prior turns injected into context |
| `STREAM` | bool | `true` | Default streaming mode for responses |
| `STREAM_KEEPALIVE_INTERVAL_SECONDS` | float | `60.0` | SSE heartbeat interval to prevent proxy timeouts |
| `STREAM_SSE_MAX_RESPONSE_CHARS` | int | `200` | Max characters per SSE chunk |

### OpenAI

| Variable | Type | Default | Description |
|----------|------|---------|-------------|
| `OPENAI_API_KEY` | str\|None | `null` | OpenAI API key (required when `DEFAULT_LITE_MODEL` is an OpenAI model) |

### Google Gemini

| Variable | Type | Default | Description |
|----------|------|---------|-------------|
| `GEMINI_API_KEY` | str\|None | `null` | Google Gemini API key — required for default chat generation |
| `GEMINI_LANGCHAIN_THINKING_LEVEL` | str | `high` | Gemini thinking budget: `minimal` \| `low` \| `medium` \| `high` |

### Anthropic Claude

| Variable | Type | Default | Description |
|----------|------|---------|-------------|
| `ANTHROPIC_API_KEY` | str\|None | `null` | Anthropic API key for Claude models |

### Novita AI

| Variable | Type | Default | Description |
|----------|------|---------|-------------|
| `NOVITA_API_KEY` | str\|None | `null` | Novita AI API key |
| `NOVITA_API_BASE` | str | `https://api.novita.ai/v3/openai` | Novita base URL |
| `NOVITA_MODEL` | str | `openai/gpt-oss-120b` | Novita generation model |
| `NOVITA_TINY_MODEL` | str | `openai/gpt-oss-120b` | Novita lite model |

### Local vLLM

| Variable | Type | Default | Description |
|----------|------|---------|-------------|
| `LOCAL_VLLM_BASE_URL` | str | `http://localhost:8000` | vLLM OpenAI-compatible endpoint |
| `LOCAL_VLLM_MODEL` | str | `gpt-oss-120b` | Model name as registered in vLLM |
| `LOCAL_VLLM_API_KEY` | str | `sk-no-key-required` | Dummy key for vLLM (no auth needed locally) |

---

## Embedding Models

| Variable | Type | Default | Description |
|----------|------|---------|-------------|
| `EMBEDDING_MODEL` | enum | `qwen` | Embedding provider: `qwen` \| `openai` \| `novita_qwen` \| `deepinfra` \| `siliconflow` |
| `EMBEDDING_DIM` | int | `2560` | Embedding vector dimension — must match the Milvus collection schema |
| `EMBEDDING_QUERY_TOKEN_LIMIT` | int | `10000` | Max tokens sent per embedding API call |

### Provider-Specific Embedding Config

| Variable | Default | Provider |
|----------|---------|----------|
| `OPENAI_EMBEDDING_MODEL` | `text-embedding-ada-002` | `openai` |
| `QWEN_EMBEDDING_URL` | `null` | `qwen` — local vLLM endpoint |
| `QWEN_EMBEDDING_MODEL` | `null` | `qwen` — model name |
| `NOVITA_EMBEDDING_BASE_URL` | `https://api.novita.ai/openai` | `novita_qwen` |
| `NOVITA_EMBEDDING_MODEL` | `qwen/qwen3-embedding-8b` | `novita_qwen` |
| `DEEPINFRA_API_KEY` | `null` | `deepinfra` |
| `DEEPINFRA_EMBEDDING_BASE_URL` | `https://api.deepinfra.com/v1/openai` | `deepinfra` |
| `DEEPINFRA_EMBEDDING_MODEL` | `Qwen/Qwen3-Embedding-4B` | `deepinfra` |
| `SILICONFLOW_API_KEY` | `null` | `siliconflow` |
| `SILICONFLOW_EMBEDDING_BASE_URL` | `https://api.siliconflow.com/v1/embeddings/` | `siliconflow` |
| `SILICONFLOW_EMBEDDING_MODEL` | `Qwen/Qwen3-Embedding-4B` | `siliconflow` |

---

## Retrieval & Search

| Variable | Type | Default | Description |
|----------|------|---------|-------------|
| `TOP_K` | int | `10` | Number of documents retrieved from the primary Milvus collection |
| `ADDITIONAL_TOP_K` | int | `3` | Documents retrieved from secondary collections (e.g. contract analyzer + main) |
| `ALPHA` | float | `0.8` | Hybrid search weight: `alpha` × dense + `(1 - alpha)` × sparse BM25 |
| `MAX_QUERY_LENGTH` | int | `5000` | Max characters in a chat query before rejection |
| `FILE_CONTENT_TOKEN_LIMIT` | int | `50000` | Max tokens from an uploaded file passed to the LLM |
| `RETRIEVAL_CONTEXT_TOKEN_LIMIT` | int | `300000` | Max combined tokens for assistant + upload context |
| `FILE_SEARCH_TOP_K` | int | `3` | Top-K for file-scoped retrieval from `project_files` collection |
| `TAVILY_API_KEY` | str\|None | `null` | Tavily web search API key (enables web fallback when corpus is insufficient) |

---

## Speech-to-Text

| Variable | Type | Default | Description |
|----------|------|---------|-------------|
| `SPEECH_TO_TEXT_PROVIDER` | enum | `azure` | `azure` or `google` |
| `AZURE_SPEECH_KEY` | str\|None | `null` | Azure Cognitive Services Speech API key |
| `AZURE_SPEECH_REGION` | str\|None | `null` | Azure region (e.g. `eastus`) |

---

## File Storage — Google Cloud Storage

| Variable | Type | Default | Description |
|----------|------|---------|-------------|
| `GCS_BUCKET_NAME` | str\|None | `null` | GCS bucket for uploaded files |
| `GCS_CREDENTIALS_PATH` | str\|None | `null` | Path to GCS service account JSON (e.g. `/app/src/security/gcs_creds.json`) |
| `GCS_PROJECT_ID` | str\|None | `null` | GCP project ID |

---

## OCR

| Variable | Type | Default | Description |
|----------|------|---------|-------------|
| `DATALAB_API_KEY` | str\|None | `null` | Datalab OCR API key for extracting text from uploaded documents |

---

## Payments — Payme/Paycom

| Variable | Type | Default | Description |
|----------|------|---------|-------------|
| `PAYME_MERCHANT_ID` | str\|None | `null` | Payme merchant ID |
| `PAYME_MERCHANT_KEY` | str\|None | `null` | Payme merchant key (used for Basic Auth on callbacks) |
| `PAYME_PAYMENT_LINK_BASE` | str | `https://checkout.paycom.uz/` | Base URL for Payme checkout links |
| `PAYME_FISCAL_RECEIPT_TITLE` | str | `Online Payment` | Fiscal receipt line item title |
| `PAYME_FISCAL_IKPU_CODE` | str\|None | `null` | IKPU product code for fiscalization |
| `PAYME_FISCAL_PACKAGE_CODE` | str\|None | `null` | Package code for fiscalization |
| `PAYME_FISCAL_VAT_PERCENT` | int | `15` | VAT percentage on transactions |
| `PAYME_FISCAL_RECEIPT_TYPE` | int | `0` | Fiscal receipt type |

### Payme Subscription Prices (in UZS sum)

| Variable | Default | Plan |
|----------|---------|------|
| `PAYME_SUBSCRIPTION_DAILY_PRICE_SUM` | `15000` | Day pass (300 credits/day) |
| `PAYME_SUBSCRIPTION_STANDARD_MONTHLY_PRICE_SUM` | `300000` | Standard monthly |
| `PAYME_SUBSCRIPTION_STANDARD_YEARLY_PRICE_SUM` | `3000000` | Standard yearly |
| `PAYME_SUBSCRIPTION_PRO_MONTHLY_PRICE_SUM` | `600000` | Pro monthly |
| `PAYME_SUBSCRIPTION_PRO_YEARLY_PRICE_SUM` | `6000000` | Pro yearly |
| `PAYME_SUBSCRIPTION_TEST_MONTHLY_PRICE_SUM` | `10000` | Test plan monthly |
| `PAYME_SUBSCRIPTION_TEST_YEARLY_PRICE_SUM` | `20000` | Test plan yearly |

---

## Payments — Click

| Variable | Type | Default | Description |
|----------|------|---------|-------------|
| `CLICK_MERCHANT_ID` | int\|None | `null` | Click merchant ID |
| `CLICK_SERVICE_ID` | int\|None | `null` | Click service ID |
| `CLICK_SECRET_KEY` | str\|None | `null` | Click HMAC signing key |
| `CLICK_MERCHANT_USER_ID` | int\|None | `null` | Click merchant user ID |
| `CLICK_PAYMENT_LINK_BASE` | str | `https://my.click.uz/services/pay` | Base URL for Click payment links |

---

## Credits & Rate Limits

| Variable | Type | Default | Description |
|----------|------|---------|-------------|
| `DAILY_CREDITS_LIMIT` | int | `100` | Default daily credit pool for users without a subscription |
| `CREDIT_COST_MAIN_ASSISTANT` | int | `10` | Credits consumed per `main` assistant request |
| `CREDIT_COST_SOLIQ_ASSISTANT` | int | `20` | Credits consumed per `tax` assistant request |
| `CREDIT_COST_SUD_ASSISTANT` | int | `25` | Credits consumed per court assistant request |
| `CREDIT_COST_SHARTNOMA_ASSISTANT` | int | `25` | Credits consumed per `contract_analyzer` request |

---

## Memory — Mem0AI

| Variable | Type | Default | Description |
|----------|------|---------|-------------|
| `MEM0_API_KEY` | str\|None | `null` | Mem0AI API key for persistent long-term memory |
| `MEM0_PROJECT_ID` | str\|None | `null` | Mem0AI project ID |
| `MEM0_ORG_ID` | str\|None | `null` | Mem0AI organization ID |

---

## CRM — Bitrix24

| Variable | Type | Default | Description |
|----------|------|---------|-------------|
| `BITRIX24_WEBHOOK_URL` | str\|None | `null` | Bitrix24 REST webhook URL for CRM lead creation |
| `BITRIX24_LEAD_TITLE` | str | `Wakil platform` | Lead title in Bitrix24 |
| `BITRIX24_LEAD_SOURCE_ID` | str\|None | `null` | Bitrix24 lead source ID |
| `BITRIX24_LEAD_SOURCE_DESCRIPTION` | str | `Wakil platforma` | Lead source description |
| `BITRIX24_LEAD_ASSIGNED_BY_ID` | int\|None | `null` | Responsible user ID in Bitrix24 |
| `BITRIX24_TIMEOUT_SECONDS` | float | `10.0` | HTTP timeout for Bitrix24 API calls |

---

## Observability — Langfuse

| Variable | Type | Default | Description |
|----------|------|---------|-------------|
| `LANGFUSE_TRACING_ENABLED` | bool | `false` | Enable LLM call tracing via Langfuse |
| `LANGFUSE_PUBLIC_KEY` | str\|None | `null` | Langfuse project public key |
| `LANGFUSE_SECRET_KEY` | str\|None | `null` | Langfuse project secret key |
| `LANGFUSE_BASE_URL` | str\|None | `null` | Langfuse instance URL (default: `https://cloud.langfuse.com`) |
| `LANGFUSE_HOST` | str\|None | `null` | Alias for `LANGFUSE_BASE_URL` |
| `TRACING` | bool | `false` | Reserved for future OpenTelemetry integration |

---

## Related

- [Explanation: Credit System](explanation-credit-system.md)
- [How-To: Switch LLM Provider](howto-switch-llm-provider.md)
- [How-To: Configure Payments](howto-configure-payments.md)
- [How-To: Enable Persistent Memory](howto-enable-memory.md)
- [How-To: Monitor Usage](howto-monitor-usage.md)
- [How-To: Deploy to Production](howto-deploy-production.md)
