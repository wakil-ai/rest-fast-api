# How to Run All Dev Servers Locally

Four processes make up the local stack, plus two datastores. Each server runs in
its own terminal.

| # | Process | Repo | Port |
|---|---------|------|------|
| 1 | Backend API | `rest-fast-api` | 8080 |
| 2 | Frontend | `frontend-nuxt` | 3000 |
| 3 | LLM API | `rest-api-ai` | 8000 |
| 4 | Celery worker | `rest-api-ai` | — |

All three repos are siblings under the same parent directory.

## 0. Datastores

MongoDB and Redis must be running before any server starts. Neither is optional:
the backend refuses to serve requests without Mongo, and Celery uses Redis as its
broker.

### Redis

`rest-fast-api/compose.yml` and `rest-api-ai/compose.yml` both define a Redis
service. One instance is enough for the whole stack — the backend, the LLM API,
and the worker all point at `localhost:6379` by default.

```bash
docker compose -f compose.yml up -d redis
```

Or standalone:

```bash
docker run -d --name wakil-redis -p 6379:6379 redis/redis-stack-server:7.4.0-v3
```

Or a locally installed Redis (`redis-server`, `brew services start redis`,
`systemctl start redis`) — anything listening on 6379 works.

### MongoDB

No compose file defines Mongo, so run it yourself:

```bash
docker run -d --name wakil-mongo -p 27017:27017 mongo:7
```

Or a local install (`mongod`, `brew services start mongodb-community`,
`systemctl start mongod`).

A remote MongoDB Atlas cluster works too — just point `MONGODB_URI` at it.

### Verify both are up

```bash
redis-cli ping                       # PONG
mongosh --eval 'db.runCommand({ping:1})'   # { ok: 1 }
```

Only `rest-fast-api` talks to MongoDB. `rest-api-ai` uses Milvus and Neo4j
instead, which are typically hosted (Zilliz / Aura) rather than local — check its
`/api/v1/health` endpoint after boot.

## 1. Backend — `rest-fast-api` (port 8080)

```bash
cd ~/akbarDev/hbai/wakil/rest-fast-api
python -m venv .venv                 # first time only
. .venv/bin/activate
pip install -r requirements.txt      # first time only
cp .env.example .env                 # first time only, then fill it in
uvicorn src.main:app --host 0.0.0.0 --port 8080 --reload
```

Settings that matter locally: `MONGODB_URI`, `MONGODB_DB_NAME`,
`LLM_SERVICE_URL` (`http://localhost:8000`),
`LLM_SERVICE_INTERNAL_TOKEN`, `API_KEY`, `SUPER_ADMIN_API_KEY`, and the `GCS_*`
credentials if you touch file upload or avatars.

Provider keys (OpenAI, Gemini, Milvus, Neo4j, Mem0, OCR, STT) belong in
`rest-api-ai`, not here.

Verify:

```bash
curl http://localhost:8080/health
```

## 2. LLM API — `rest-api-ai` (port 8000)

The project is not a `uv` project — it is plain FastAPI, so run it with
`uvicorn` from its own virtualenv:

```bash
cd ~/akbarDev/hbai/wakil/rest-api-ai
.venv/bin/uvicorn --app-dir src api.main:app --port 8000 --reload
```

`--app-dir src` replaces `PYTHONPATH=src` — no exported variable needed, and no
extra directories to create. The port is set by the config file, so pass `--port`
on the command line rather than through the environment.

Verify — note that every route sits under the `/api/v1` prefix
(`src/api/router.py:17`), unlike `rest-fast-api`. Bare `/health` returns a JSON
404 (`"Nothing matches the given URI."`), which still means the service booted:

```bash
curl http://localhost:8000/api/v1/health | jq .
```

A healthy response is `"message": "Everything is OK."` with checks for `api`,
`redis`, `milvus`, and `neo4j`. Milvus and Neo4j come from the remote
credentials in that project's `.env`.

Other prefixed paths: `/api/v1/ping`, `/api/v1/docs`, `/api/v1/openapi.json`.
The unprefixed versions 307-redirect rather than serve.

## 3. Celery worker — `rest-api-ai`

Uploaded files are processed **only** by this worker. The backend enqueues
`ocr.process_file` and `documents.process` into Redis and returns `202`
immediately; with no worker running the job sits in the queue, the file's status
stays `queued`, and the request that waits on it fails after 15 minutes with
`Document processing timed out.` (`DOCUMENT_PROCESSING_POLL_TIMEOUT_SECONDS`,
default 900).

`celery` has no `--app-dir` flag, so this one does need `PYTHONPATH`:

```bash
cd ~/akbarDev/hbai/wakil/rest-api-ai
PYTHONPATH=src .venv/bin/celery -A shared.externals.celery:celery_app worker \
  --loglevel=INFO --concurrency=1
```

`No module named 'shared'` means `PYTHONPATH=src` did not reach that shell.

The worker needs its own provider settings on top of the API's: `MISTRAL_API_KEY`
(Mistral OCR), `OPENAI_API_KEY` (embeddings), `MILVUS_URI`, and Redis. If the
venv was built from API dependencies alone, add the worker dependencies:

```bash
.venv/bin/pip install -r requirements.txt -r requirements/requirements.worker.txt
```

Skip this process if you are not testing file upload or OCR. Chat, drafts, and
everything else work without it.

## 4. Frontend — `frontend-nuxt` (port 3000)

```bash
cd ~/akbarDev/hbai/wakil/frontend-nuxt
pnpm install                         # first time only
pnpm dev
```

Nuxt listens on 3000 by default; override with `pnpm dev --port 3001`. Its `.env`
must point at the backend (`http://localhost:8080`) — the Nuxt server proxies
every API call and swaps the shared API key for a per-user bearer JWT, so calling
the backend directly from the browser is not the supported path.

Open http://localhost:3000.

## Start order

1. MongoDB + Redis
2. `rest-api-ai` API (8000)
3. Celery worker — only if testing file processing
4. `rest-fast-api` (8080)
5. `frontend-nuxt` (3000)

The backend calls the LLM API at request time, not at boot, so a strict order is
not enforced — but starting bottom-up keeps the first request from failing.

## Troubleshooting

**Port already in use** — `lsof -i :8080` (or 8000 / 3000), then kill the holder.

**Backend boots but every request 500s** — MongoDB is unreachable. Check
`MONGODB_URI` and that Mongo is actually listening on 27017.

**File upload returns 202, then times out after 15 minutes** — the Celery worker
is not running. See step 3.

**Celery: `No module named 'shared'`** — `PYTHONPATH=src` missing.

**LLM API ignores your port environment variable** — that project builds its
config with keyword arguments, which beat environment variables. Use the
`--port` flag.

**Frontend: `Failed to resolve import ...`** — a pnpm linking problem, not a
backend one. Reinstall (`rm -rf node_modules .nuxt && pnpm install`) before
looking anywhere else.
