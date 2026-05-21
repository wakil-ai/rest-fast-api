# Tutorial: Stream a Deep-Research Answer

In this tutorial you'll call the `/api/v3/chat/agent/stream` endpoint, which returns an answer as a real-time SSE (Server-Sent Events) stream. Deep-research mode enables the Tavily web search fallback when the legal corpus doesn't have enough context.

**Time to complete:** ~15 minutes  
**Prerequisites:** Complete [Tutorial: First Chat](tutorial-first-chat.md) — you need a running API, a user, and a session.

---

## What you'll build

A Python script that:
1. Sends a deep-research request.
2. Reads the SSE stream chunk by chunk.
3. Reassembles and prints the final answer with metadata.

---

## Step 1: Understand the SSE event format

The streaming endpoint emits newline-delimited SSE events. Each event is:

```
data: <JSON payload>\n\n
```

Event types you'll receive in order:

| Event `type` | When | Contents |
|-------------|------|----------|
| `metadata` | First event | `session_id`, `message_id`, `assistant_name` |
| `attachments` | After metadata (if any) | DOCX links for contract analyzer |
| `text_delta` | During generation | `content`: one chunk of text |
| `think` | During Gemini thinking (optional) | Internal reasoning (may be empty) |
| `end` | Last event | `message_id`, `latency_ms`, final `attachments` |

---

## Step 2: Create a Python streaming client

Create `stream_example.py`:

```python
import requests
import json

API_URL = "http://localhost:8080"
API_KEY = "dev-key"

def stream_legal_question(user_id, session_id, query):
    url = f"{API_URL}/api/v3/chat/agent/stream"
    headers = {
        "admin": API_KEY,
        "Content-Type": "application/json",
        "Accept": "text/event-stream",
    }
    payload = {
        "user_id": user_id,
        "session_id": session_id,
        "query": query,
        # deep_research=True enables Tavily web fallback when corpus is insufficient
        # This is set via the assistant configuration; the agent/stream endpoint
        # always enables the full orchestration graph.
    }

    answer_chunks = []
    metadata = {}

    with requests.post(url, headers=headers, json=payload, stream=True) as response:
        response.raise_for_status()

        for line in response.iter_lines():
            if not line:
                continue
            if not line.startswith(b"data: "):
                continue

            raw = line[len(b"data: "):]
            try:
                event = json.loads(raw)
            except json.JSONDecodeError:
                continue

            event_type = event.get("type")

            if event_type == "metadata":
                metadata = event
                print(f"[metadata] session={event.get('session_id')} message={event.get('message_id')}")

            elif event_type == "text_delta":
                chunk = event.get("content", "")
                answer_chunks.append(chunk)
                print(chunk, end="", flush=True)

            elif event_type == "attachments":
                attachments = event.get("attachments", [])
                if attachments:
                    print(f"\n[attachments] {attachments}")

            elif event_type == "end":
                print(f"\n\n[done] latency={event.get('latency_ms')}ms")

    return "".join(answer_chunks)


if __name__ == "__main__":
    answer = stream_legal_question(
        user_id="tutorial-user-1",
        session_id="sess_abc123",   # use the session_id from Tutorial 1
        query="Korrupsiyaga qarshi kurash to'g'risidagi qonunning asosiy qoidalari nima?",
    )
    print(f"\nFull answer ({len(answer)} chars)")
```

---

## Step 3: Run the streaming client

```bash
python stream_example.py
```

You'll see text appear progressively as Gemini generates each chunk:

```
[metadata] session=sess_abc123 message=msg_def456
O'zbekiston Respublikasining "Korrupsiyaga qarshi kurash to'g'risida"gi Qonuni...
...
[done] latency=8432ms

Full answer (1247 chars)
```

---

## Step 4: Try with Tavily web search

If `TAVILY_API_KEY` is set in `.env`, the pipeline evaluates context quality and falls back to web search when the corpus is insufficient. To trigger this, ask about a very recent event or amendment:

```python
answer = stream_legal_question(
    user_id="tutorial-user-1",
    session_id="sess_abc123",
    query="2024-yilda qabul qilingan soliq o'zgarishlari haqida gapiring",
)
```

If Tavily fires, you'll see a slightly longer latency (~2–5 seconds more) as web search runs before generation.

---

## Step 5: Handle the contract analyzer with attachments

When using `assistant: "contract_analyzer"`, the response may include a DOCX attachment:

```python
payload = {
    "user_id": "tutorial-user-1",
    "session_id": "sess_abc123",
    "query": "Ijara shartnomasi shablonini tayyorlab bering",
    "assistant": "contract_analyzer",
}
```

In this case the `attachments` event arrives with a signed GCS URL:

```
[attachments] [{"type": "docx", "url": "https://storage.googleapis.com/..."}]
```

---

## What you built

You now know how to:
- Connect to the SSE stream and parse events.
- Reassemble a complete answer from `text_delta` chunks.
- Handle metadata and attachments.
- Understand when Tavily web search is invoked.

**What's next:**
- [Tutorial: Upload a File and Chat About It](tutorial-file-upload.md)
- [Explanation: LangGraph Orchestration](explanation-orchestration.md) — what happens inside the stream

---

## Troubleshooting

| Symptom | Likely cause |
|---------|-------------|
| Stream returns all at once | Nginx `proxy_buffering` is on — add `proxy_buffering off;` to your proxy config |
| `TransferEncodingError` | Upstream Gemini dropped the connection; retry the request |
| No text chunks received | Check that `STREAM=true` in `.env`; check API logs for errors during generation |
| Web search never fires | `TAVILY_API_KEY` not set, or the corpus had sufficient context |
