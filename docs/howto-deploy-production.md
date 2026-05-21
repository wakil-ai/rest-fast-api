# How to Deploy to Production

This guide sets up the full WakilAI REST API stack on a production server: API, Redis Stack, MongoDB, Milvus, and a reverse proxy.

## Prerequisites

- Docker 24+ and Docker Compose v2
- A server with ≥8 GB RAM (16 GB recommended for Milvus + API × 4 replicas)
- Ports 8080 (API), 6379 (Redis), 27017 (Mongo), 19530 (Milvus) not exposed publicly (use a reverse proxy)
- A `.env` file with all required values (copy from `.env.example`)

---

## Steps

### 1. Clone the repository

```bash
git clone https://github.com/wakil-ai/rest-api.git
cd rest-api
```

### 2. Configure environment

```bash
cp .env.example .env
```

Edit `.env`. These are required for the API to start:

```env
# Security — change ALL of these
API_KEY=your-strong-api-key-here
SUPER_ADMIN_API_KEY=your-strong-super-admin-key
AUTH_SECRET_KEY=random-32-char-string-here
DOCS_USER=youruser
DOCS_PASSWORD=yourpassword

# App
DEBUG=false
HOST_URL=https://api.yourdomain.com
ALLOWED_ORIGINS=["https://chat.yourdomain.com"]

# MongoDB
MONGODB_URI=mongodb://mongo:27017
MONGODB_DB_NAME=wakilai

# Vector DB
VECTOR_DB_TYPE=milvus
MILVUS_URI=http://milvus:19530

# Redis (already set by compose.yml via environment override)
REDIS_HOST=redis
REDIS_PORT=6379

# LLM
GEMINI_API_KEY=your-gemini-key
OPENAI_API_KEY=your-openai-key
DEFAULT_CHAT_MODEL=gemini-3.1-pro-preview
DEFAULT_LITE_MODEL=gpt-4.1-mini

# Embedding
EMBEDDING_MODEL=siliconflow
SILICONFLOW_API_KEY=your-siliconflow-key
EMBEDDING_DIM=2560
```

### 3. Start Redis and the API

The included `compose.yml` runs 4 API replicas + Redis Stack:

```bash
docker compose -f compose.yml up -d
```

Verify it's healthy:

```bash
docker compose ps
curl http://localhost:8080/health
```

Expected: `{"status":"ok"}` (or similar).

### 4. Start MongoDB

The API expects MongoDB externally. Options:

**Option A — Docker (development/single-node):**

```bash
docker run -d \
  --name mongo \
  --network rest-api_wakilai-bridge \
  -v mongo_data:/data/db \
  -p 27017:27017 \
  mongo:7
```

**Option B — MongoDB Atlas (recommended for production):**

Set `MONGODB_URI=mongodb+srv://user:pass@cluster.mongodb.net/wakilai` in `.env`.

### 5. Start Milvus

Milvus requires etcd and MinIO as storage backends. Use the official standalone compose:

```bash
wget https://github.com/milvus-io/milvus/releases/download/v2.4.0/milvus-standalone-docker-compose.yml
docker compose -f milvus-standalone-docker-compose.yml up -d
```

Check status:

```bash
curl http://localhost:9091/healthz
```

Alternatively, use [Zilliz Cloud](https://zilliz.com) (hosted Milvus) and set `MILVUS_URI=https://...` and `MILVUS_TOKEN=your-token`.

### 6. Set up a reverse proxy (Nginx example)

The API runs on port 8080 behind a load balancer (4 replicas with round-robin). Expose it via HTTPS:

```nginx
upstream wakilai_api {
    server 127.0.0.1:8080;
    keepalive 64;
}

server {
    listen 443 ssl http2;
    server_name api.yourdomain.com;

    ssl_certificate /etc/ssl/certs/api.yourdomain.com.crt;
    ssl_certificate_key /etc/ssl/private/api.yourdomain.com.key;

    location / {
        proxy_pass http://wakilai_api;
        proxy_http_version 1.1;
        proxy_set_header Connection "";
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_read_timeout 300s;
        proxy_send_timeout 300s;
        # Required for SSE streaming
        proxy_buffering off;
        proxy_cache off;
    }
}
```

> `proxy_buffering off` is required for SSE streaming responses from `/api/v3/chat/agent/stream`. Without it, Nginx buffers the stream and the client sees nothing until the full response arrives.

### 7. Verify end-to-end

```bash
# Health check (no auth)
curl https://api.yourdomain.com/health

# Authenticated chat request
curl -X POST https://api.yourdomain.com/api/v3/chat/ask \
  -H "admin: your-strong-api-key-here" \
  -H "Content-Type: application/json" \
  -d '{
    "user_id": "test-user-1",
    "session_id": "test-session-1",
    "query": "What are the overtime rules in the Labor Code?",
    "assistant": "main",
    "stream": false
  }'
```

---

## Scaling

The default `compose.yml` runs 4 replicas on a single node (ports 8080–8083). For multi-node deployments:

- Remove the port range mapping and use a load balancer (e.g. Cloudflare, HAProxy) in front.
- All API replicas share MongoDB, Milvus, and Redis — no local state is kept in the API process.
- Redis is a single instance; the LangGraph checkpointer requires RediSearch + RedisJSON (no Redis Cluster support in `AsyncRedisSaver`).

---

## Resource limits

The compose.yml sets sensible defaults:

| Service | CPU limit | Memory limit |
|---------|-----------|-------------|
| API (×4) | 2 vCPU | 4 GB each |
| Redis Stack | 2 vCPU | 4 GB |

Adjust under the `deploy.resources` section for your server.

---

## Verification

- [ ] `GET /health` returns 200
- [ ] Chat request returns a non-empty `answer`
- [ ] Redis checkpoint is created: `redis-cli KEYS "langgraph:*"` shows entries after a chat
- [ ] MongoDB has a `messages` document after a chat: `db.messages.find().limit(1)`
- [ ] Milvus reports collections loaded: check Milvus logs for `"loaded collection"`

---

## Troubleshooting

| Problem | Fix |
|---------|-----|
| API exits immediately | Check `docker compose logs api` — likely a missing env var |
| `NotImplementedError` on first chat | Redis is plain Redis, not Redis Stack. Run `redis/redis-stack-server:7.4.0-v3`. |
| 401 on every request | `API_KEY_NAME` defaults to `admin`; send header `admin: your-key` not `x-api-key: your-key` |
| SSE stream delivers all at once | Nginx `proxy_buffering` is on. Add `proxy_buffering off;` to the location block. |
| Milvus connection refused | Check `MILVUS_URI` points to the correct host. From inside compose network, use service name: `http://milvus:19530`. |

---

## Related

- [How-To: Switch LLM Provider](howto-switch-llm-provider.md)
- [Reference: Environment Variables](reference-environment-variables.md)
- [Infrastructure Overview](infrastructure.md)
- [Deployment Notes (Milvus + Mongo)](deployment.md)
