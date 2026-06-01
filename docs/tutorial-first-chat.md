# Tutorial: Send Your First Legal Question

In this tutorial you'll start a running local API, create a user and session, and get an answer from the `main` (general law) assistant. By the end you'll have a working end-to-end chat call and understand the request/response shape.

**Time to complete:** ~10 minutes  
**Prerequisites:** Python 3.10+, Docker

---

## What you'll build

A script that:
1. Starts the API and its dependencies.
2. Creates a user and session.
3. Sends a legal question in Uzbek.
4. Prints the answer.

---

## Step 1: Start the dependencies

Start Redis Stack (required for LangGraph conversation memory — plain Redis will not work):

```bash
docker run -d --name wakilai-redis -p 6379:6379 redis/redis-stack-server:7.4.0-v3
```

Verify Redis is up:

```bash
docker exec wakilai-redis redis-cli ping
# Expected: PONG
```

You'll also need MongoDB and Milvus. For a quick start, use shared dev instances from your team. Or see [deployment.md](deployment.md) for local Docker commands.

---

## Step 2: Configure environment

```bash
cp .env.example .env
```

Edit `.env` with the minimum required values:

```env
DEBUG=true
API_KEY=dev-key

MONGODB_URI=mongodb://localhost:27017
MONGODB_DB_NAME=wakilai

MILVUS_URI=http://localhost:19530
VECTOR_DB_TYPE=milvus

REDIS_HOST=localhost
REDIS_PORT=6379

GEMINI_API_KEY=your-gemini-key
OPENAI_API_KEY=your-openai-key
DEFAULT_LITE_MODEL=gpt-4.1-mini
DEFAULT_CHAT_MODEL=gemini-3.1-pro-preview

EMBEDDING_MODEL=siliconflow
SILICONFLOW_API_KEY=your-siliconflow-key
```

---

## Step 3: Start the API

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
uvicorn app.main:app --host 0.0.0.0 --port 8080 --reload
```

Health check — this should return immediately:

```bash
curl http://localhost:8080/health
# {"status": "ok"}
```

You'll see the API register Milvus collections in the startup logs. If you see `Failed to load collection`, check that `MILVUS_URI` is correct and Milvus is running.

---

## Step 4: Create a user

The API requires a user record before accepting chat requests.

```bash
curl -X POST http://localhost:8080/api/v2/history/users \
  -H "admin: dev-key" \
  -H "Content-Type: application/json" \
  -d '{
    "id": "tutorial-user-1",
    "first_name": "Test",
    "last_name": "User",
    "auth_method": "api"
  }'
```

Expected response:

```json
{
  "_id": "tutorial-user-1",
  "first_name": "Test",
  "last_name": "User",
  "created_at": "2026-05-21T..."
}
```

---

## Step 5: Create a session

Each conversation lives in a session. Create one for the user:

```bash
curl -X POST http://localhost:8080/api/v2/history/sessions \
  -H "admin: dev-key" \
  -H "Content-Type: application/json" \
  -d '{
    "user_id": "tutorial-user-1",
    "title": "My first session"
  }'
```

Note the `_id` in the response — this is your `session_id`:

```json
{
  "_id": "sess_abc123",
  "user_id": "tutorial-user-1",
  "title": "My first session",
  "created_at": "2026-05-21T..."
}
```

---

## Step 6: Ask a legal question

Send a question using the `main` assistant (general Uzbek law):

```bash
curl -X POST http://localhost:8080/api/v3/chat/ask \
  -H "admin: dev-key" \
  -H "Content-Type: application/json" \
  -d '{
    "user_id": "tutorial-user-1",
    "session_id": "sess_abc123",
    "query": "Ish beruvchi ishchiga necha kun ta'til berishi shart?",
    "assistant": "main",
    "stream": false
  }'
```

Expected response:

```json
{
  "answer": "O'zbekiston Mehnat kodeksining 134-moddasiga ko'ra...",
  "session_id": "sess_abc123",
  "message_id": "msg_xyz789",
  "latency_ms": 3450,
  "attachments": null
}
```

You got a legal answer grounded in the Uzbek Labor Code.

---

## Step 7: Ask a follow-up question

The API remembers your conversation. Ask a follow-up without repeating context:

```bash
curl -X POST http://localhost:8080/api/v3/chat/ask \
  -H "admin: dev-key" \
  -H "Content-Type: application/json" \
  -d '{
    "user_id": "tutorial-user-1",
    "session_id": "sess_abc123",
    "query": "Bu ta'\''tilni qachon olish mumkin?",
    "assistant": "main",
    "stream": false
  }'
```

The response references the previous answer about vacation rules because the conversation history is preserved in the Redis checkpoint.

---

## What you built

You now have:
- A working API with user and session management.
- A chat call that retrieves Uzbek legal documents from Milvus and generates an answer with Gemini.
- Stateful conversations using Redis LangGraph checkpointing.

**What's next:**
- [Tutorial: Stream a Deep-Research Answer](tutorial-streaming.md) — real-time SSE responses.
- [Tutorial: Upload a File and Chat About It](tutorial-file-upload.md) — analyze a specific document.
- [API Reference](api-reference.md) — full endpoint listing.

---

## Troubleshooting

| Symptom | Likely cause |
|---------|-------------|
| `401 Unauthorized` | Header name is `admin` (not `x-api-key`) with value `dev-key` |
| `User not found` | Didn't create user in Step 4 first |
| `Session not found` | Didn't create session in Step 5, or `session_id` typo |
| Empty answer with `latency_ms: 0` | Milvus collection is empty — no documents indexed yet |
| `GEMINI_API_KEY is required` | Key missing from `.env` |
