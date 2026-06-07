# WakilAI Rest API

`rest-api` is the public/core backend. It owns authentication, credits, MongoDB chat history, projects, payments, storage, public route compatibility, and attachment persistence.

AI execution is delegated to the internal `rest-api-llm` service through `LlmServiceClient`.

## Responsibilities

- Public API routing for `/api/v2/*` and `/api/v3/*`
- Auth, OTP, referrals, fingerprints, promo codes, subscriptions, and payments
- MongoDB users, sessions, messages, files, projects, and transaction records
- Google Cloud Storage upload/download paths for user files and generated DOCX attachments
- Public memory and speech-to-text route compatibility by proxying to `rest-api-llm`
- File upload validation, temp spooling, content hash reuse, OCR/embed proxy calls, and project stats
- Internal criminal excerpt bridge used by `rest-api-llm`

## Not Owned Here

These belong in `rest-api-llm`:

- Chat orchestration and assistant logic
- LLM provider clients
- Embeddings and vector indexing/search
- OCR
- Speech-to-text execution
- Mem0/memory execution
- Web search and retrieval logic
- Criminal assistant graph/vector retrieval

## Required Services

- MongoDB
- Redis
- Google Cloud Storage credentials, when file persistence is enabled
- `rest-api-llm`, reachable through `LLM_SERVICE_URL`

## Environment

Copy `.env.example` and configure the public backend:

```bash
cp .env.example .env
```

Important settings:

- `MONGODB_URI`, `MONGODB_DB_NAME`
- `LLM_SERVICE_URL`
- `LLM_SERVICE_INTERNAL_TOKEN`
- `API_KEY`, `SUPER_ADMIN_API_KEY`
- `GCS_BUCKET_NAME`, `GCS_CREDENTIALS_PATH`, `GCS_PROJECT_ID`
- payment provider settings, if payments are enabled

Provider keys such as OpenAI, Gemini, Milvus, Neo4j, Mem0, OCR, and STT keys should be configured in `rest-api-llm`, not here.

## Run Locally

```bash
python -m venv .venv
. .venv/bin/activate
pip install -r requirements.txt
uvicorn app.main:app --host 0.0.0.0 --port 8080 --reload
```

Health checks:

- `GET /`
- `GET /health`

Protected docs:

- `GET /docs`
- `GET /redoc`
- `GET /openapi.json`

## Main Route Groups

- Chat: `/api/v2/chat/*`, `/api/v3/chat/*`
- History/projects/files: `/api/v2/history/*`
- Memory proxy: `/api/v2/memory/*`
- Speech-to-text proxy: `/api/v2/speech-to-text/*`
- Auth/OTP: `/api/v2/auth/*`, `/api/v2/otp/*`
- Payments: `/api/v2/transaction/*`
- Promo codes: `/api/v2/promo-codes/*`
- Referrals: `/api/v2/referrals/*`
- Admin: `/api/v2/admin/*`
- Internal bridge: `/internal/*`

## Tests

```bash
python -m pytest -q
```

The service should start without AI provider, vector database, OCR, STT, or Mem0 keys. The only AI-related configuration needed here is the internal `rest-api-llm` URL/token pair.
