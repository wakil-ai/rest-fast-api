# How to Enable Persistent User Memory

WakilAI has two memory layers:

1. **Short-term (per-session):** LangGraph + Redis checkpointer. Active by default — every session's conversation history is stored in Redis and injected into subsequent turns automatically.
2. **Long-term (cross-session):** Mem0AI. Optional — stores important facts about a user across sessions (e.g. "user is a tax lawyer", "user prefers Uzbek responses").

This guide covers setting up Mem0AI long-term memory.

## Prerequisites

- A Mem0AI account at [mem0.ai](https://mem0.ai)
- Access to `.env`

---

## Steps

### 1. Get your Mem0AI credentials

Log in to [mem0.ai](https://mem0.ai), create a project, and note:
- API Key
- Project ID
- Organization ID

### 2. Add credentials to `.env`

```env
MEM0_API_KEY=your-mem0-api-key
MEM0_PROJECT_ID=your-project-id
MEM0_ORG_ID=your-org-id
```

### 3. Restart the API

```bash
docker compose restart api
```

### 4. Save a memory for a user

```bash
curl -X POST http://localhost:8080/api/v2/memory/save/ \
  -H "admin: your-api-key" \
  -H "Content-Type: application/json" \
  -d '{
    "user_id": "user_abc",
    "interaction": {
      "role": "user",
      "content": "I am a tax lawyer specializing in international trade."
    }
  }'
```

Mem0AI extracts and stores relevant facts from the interaction.

### 5. Retrieve memories for a user

```bash
curl "http://localhost:8080/api/v2/memory/user/user_abc/" \
  -H "admin: your-api-key"
```

Returns a list of stored memory items with their IDs.

### 6. Verify memory injection into chat

Send a chat request for the user. The `load_long_term_memory` node in the orchestration graph automatically queries the LangGraph store for up to 5 memories and injects them into the context window before generation.

To confirm memory is being used, enable Langfuse tracing and inspect the `long_term_memory` field in the `load_long_term_memory` trace span.

---

## Memory endpoints

| Method | Endpoint | Description |
|--------|----------|-------------|
| `GET` | `/api/v2/memory/user/{user_id}/` | List all memories for a user |
| `POST` | `/api/v2/memory/save/` | Save an interaction as memory |
| `PUT` | `/api/v2/memory/update/{memory_id}` | Update a specific memory |
| `DELETE` | `/api/v2/memory/{memory_id}/` | Delete a specific memory |

---

## How memory is injected

The `load_long_term_memory` graph node searches the LangGraph store:

```python
namespace = ("legal_assistant", "users", user_id, "memories")
memories = store.search(namespace, limit=5)
memory_text = "\n".join(str(item.value) for item in memories)
state["long_term_memory"] = memory_text
```

The `memory_text` is passed to the `rewrite_query` node, which uses it to contextualize the query rewrite. If Mem0AI returns "User is a tax lawyer", the rewriter can produce more precise retrieval queries without the user re-explaining their background each time.

---

## Short-term memory (Redis checkpointer)

Short-term memory requires no setup beyond Redis Stack:

```env
LANGGRAPH_CHECKPOINT_USE_REDIS=true
REDIS_HOST=localhost
REDIS_PORT=6379
```

Redis stores the full LangChain message thread per session. The last `CHAT_HISTORY_LIMIT` (default: 3) turns are injected as context in every turn.

Session state expires after `LANGGRAPH_CHECKPOINT_TTL_SECONDS` (default: 3 days). Deleting a session via `DELETE /api/v2/history/sessions/{session_id}` also calls `adelete_thread()` to clean up the Redis checkpoint.

---

## Troubleshooting

| Problem | Fix |
|---------|-----|
| Memories never appear in responses | Check that `MEM0_API_KEY` is set and not null; verify via `GET /api/v2/memory/user/{user_id}/` |
| LangGraph store raises RuntimeError | `load_long_term_memory` raises if `runtime.store` is None — this means the graph was compiled without a store. Check `service.py` store initialization. |
| Session loses history after restart | Redis data is lost if using `--rm` or no volume. Mount `redis_data:/data` volume as in `compose.yml`. |

---

## Related

- [Reference: Environment Variables](reference-environment-variables.md) — `MEM0_*`, `LANGGRAPH_*`, `REDIS_*`
- [Reference: LangGraph State & Nodes](reference-langgraph-state.md) — `load_long_term_memory` node
- [Explanation: Orchestration Pipeline](explanation-orchestration.md)
