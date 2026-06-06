# How to Monitor API Usage

WakilAI has three monitoring surfaces: **Langfuse** (LLM call tracing), **MongoDB analytics** (token counts and credit usage), and **structured logs** (Loguru).

---

## Langfuse — LLM tracing

Langfuse captures every LLM call in the orchestration pipeline: latency, token usage, prompts, and output. This is the primary tool for debugging slow responses and understanding model behavior.

### 1. Set up Langfuse

Sign up at [cloud.langfuse.com](https://cloud.langfuse.com) or run [Langfuse self-hosted](https://langfuse.com/docs/deployment/self-host).

Create a project and get your public and secret keys.

### 2. Enable tracing in `.env`

```env
LANGFUSE_TRACING_ENABLED=true
LANGFUSE_PUBLIC_KEY=pk-lf-...
LANGFUSE_SECRET_KEY=sk-lf-...
LANGFUSE_BASE_URL=https://cloud.langfuse.com
```

Restart the API.

### 3. What you'll see in Langfuse

Each chat request creates a trace with these spans:

| Span name | What it captures |
|-----------|-----------------|
| `query_rewrite` | Lite LLM call — input query + history, output rewritten query |
| `recognize_intent` | Intent classification (contract requests only) |
| `route_court` | Court routing decision (court requests only) |
| `milvus_filter_generation` | Lite LLM → Milvus filter expression |
| `criminal_case_retrieval` | Neo4j + Mongo subgraph (criminal requests only) |
| `context_evaluation` | Lite LLM context sufficiency check |
| `final_answer_generation` | Generation LLM — full prompt, output, token usage |

Token usage per request is visible in the `final_answer_generation` span under **Usage**.

### 4. Identify slow requests

Filter by `latency > 10s` in Langfuse to find requests where:
- Vector retrieval is slow (Milvus connection latency)
- Generation is slow (large context, complex reasoning)
- Lite model calls add up (court routing + filter generation)

---

## MongoDB — Token counting

Every message stores token usage in the `token_counts` collection:

```json
{
  "message_id": "msg_abc",
  "user_id": "user_xyz",
  "session_id": "sess_123",
  "input_tokens": 1450,
  "output_tokens": 512,
  "total_tokens": 1962,
  "model": "gemini-3.1-pro-preview",
  "created_at": "2026-05-21T14:00:00Z"
}
```

Query total tokens per user:

```javascript
// MongoDB shell
db.token_counts.aggregate([
  { $match: { user_id: "user_xyz" } },
  { $group: { _id: "$user_id", total: { $sum: "$total_tokens" } } }
])
```

---

## MongoDB — Credit usage

The `creditusage` collection tracks daily credit consumption per user:

```javascript
// Credits used today
db.creditusage.findOne({ user_id: "user_xyz", date: "2026-05-21" })

// Top credit consumers today
db.creditusage.find({ date: "2026-05-21" })
  .sort({ credits_used: -1 })
  .limit(10)
```

Via the API:

```bash
GET /api/v2/history/users/{user_id}/rate-limit
```

Response:

```json
{
  "remaining_credits": 65,
  "effective_daily_credit_limit": 100,
  "today_credits_used": 35,
  "uses_combined_credit_pool": true
}
```

---

## Structured logs — Loguru

All application logs go through Loguru (`app/core/logger.py`). In Docker, view with:

```bash
docker compose logs -f api
```

Key log patterns to watch:

| Pattern | Meaning |
|---------|---------|
| `[RateLimitService] User ... has insufficient credits` | User hit their daily limit |
| `[MilvusHandler] Failed to load collection` | Milvus collection couldn't load at startup |
| `[MilvusExpr] Truncating text from ... to ...` | Document chunk was too large for Milvus VARCHAR |
| `[Bitrix24] Error creating lead` | CRM webhook failed |
| `Error checking credits for user` | MongoDB unavailable during credit check |

For full request tracebacks, look for `exc_info=True` log entries from `chat_service.py`.

---

## Health check

```bash
curl http://localhost:8080/health
```

Returns `200 {"status": "ok"}` when the API process is running. Does not check downstream dependencies (Milvus, Mongo, Redis) — use Langfuse and log monitoring for those.

---

## Prometheus / metrics (not yet built-in)

The API doesn't export Prometheus metrics natively. Options:

1. **Langfuse → webhook** — Langfuse can POST usage events to an external analytics pipeline.
2. **MongoDB Change Streams** — watch `token_counts` and `creditusage` collections for real-time usage aggregation.
3. **Nginx access logs** — upstream request metrics (latency, status codes) via standard Nginx log parsing.

---

## Related

- [Reference: Environment Variables](reference-environment-variables.md) — `LANGFUSE_*`
- [Reference: Error Codes](reference-error-codes.md)
- [Explanation: Credit System](explanation-credit-system.md)
