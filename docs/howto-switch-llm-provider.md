# How to Switch LLM Provider

WakilAI uses two LLM roles: a **generation model** for final answers and a **lite model** for fast routing tasks (query rewrite, intent classification, Milvus filter generation). Each can be changed independently.

## Prerequisites

- Access to `.env`
- An API key for the target provider

---

## Steps

### 1. Decide what to change

| Role | Variable | Default |
|------|----------|---------|
| Final answer generation | `DEFAULT_CHAT_MODEL` | `gemini-3.1-pro-preview` |
| Routing, rewrite, filters | `DEFAULT_LITE_MODEL` | `gpt-4.1-mini` |

You can change one or both.

### 2. Switch the generation model

**To Gemini (current default):**

```env
GEMINI_API_KEY=your-google-ai-studio-key
DEFAULT_CHAT_MODEL=gemini-3.1-pro-preview
# Optional: control reasoning depth
GEMINI_LANGCHAIN_THINKING_LEVEL=high
```

**To OpenAI GPT:**

```env
OPENAI_API_KEY=your-openai-key
DEFAULT_CHAT_MODEL=gpt-4.1
```

**To Anthropic Claude:**

```env
ANTHROPIC_API_KEY=your-anthropic-key
DEFAULT_CHAT_MODEL=claude-opus-4-5-20250929
```

**To Novita AI (OpenAI-compatible endpoint):**

```env
NOVITA_API_KEY=your-novita-key
NOVITA_MODEL=openai/gpt-oss-120b
DEFAULT_CHAT_MODEL=gpt-oss-120b
LLM_PROVIDER=novita
```

**To a local vLLM server:**

```env
LOCAL_VLLM_BASE_URL=http://localhost:8000
LOCAL_VLLM_MODEL=your-model-name
DEFAULT_CHAT_MODEL=your-model-name
LLM_PROVIDER=openai   # vLLM uses OpenAI-compatible API
OPENAI_API_KEY=sk-no-key-required
```

### 3. Switch the lite model

The lite model handles routing and is called 3–5 times per request. Use a fast, cheap model.

**OpenAI (current default):**

```env
OPENAI_API_KEY=your-openai-key
DEFAULT_LITE_MODEL=gpt-4.1-mini
```

**Gemini Flash:**

```env
GEMINI_API_KEY=your-key
DEFAULT_LITE_MODEL=gemini-3-flash-preview
```

**Novita:**

```env
NOVITA_API_KEY=your-novita-key
NOVITA_TINY_MODEL=openai/gpt-oss-120b
DEFAULT_LITE_MODEL=gpt-oss-120b
```

### 4. Restart the API

```bash
docker compose restart api
```

Or, for local dev:

```bash
# Ctrl+C to stop uvicorn, then:
uvicorn app.main:app --host 0.0.0.0 --port 8080 --reload
```

### 5. Verify

```bash
curl http://localhost:8080/api/v3/chat/model-info \
  -H "admin: your-api-key"
```

Expected response:

```json
{
  "top_k": 10,
  "alpha": 0.8,
  "temperature": 0.1,
  "service_provider": "openai",
  "embedding_model": "siliconflow",
  "stream": true
}
```

Send a test chat request and confirm the answer arrives.

---

## Supported model IDs

These model IDs are defined in `ChatModel` enum ([src/models/chat.py](../src/models/chat.py)) and accepted by the API:

| Model ID | Provider |
|----------|---------|
| `gpt-4.1` | OpenAI |
| `gpt-4.1-mini` | OpenAI |
| `gpt-4o` | OpenAI |
| `gpt-5.2` | OpenAI |
| `claude-opus-4-5-20250929` | Anthropic |
| `claude-sonnet-4-5-20250929` | Anthropic |
| `claude-haiku-4-5-20251001` | Anthropic |
| `gemini-3.1-pro-preview` | Google |
| `gemini-3-flash-preview` | Google |
| `gpt-oss-120b` | Novita / local vLLM |

---

## Switching the embedding model

The embedding model must match the model used when documents were indexed into Milvus. Changing it without re-indexing will produce garbage retrieval results.

```env
EMBEDDING_MODEL=siliconflow  # qwen | openai | novita_qwen | deepinfra | siliconflow
SILICONFLOW_API_KEY=your-key
EMBEDDING_DIM=2560            # must match the Milvus collection schema
```

**Only change the embedding model if you plan to re-index all Milvus collections.** This is a destructive operation and requires coordination with your data team.

---

## Troubleshooting

| Problem | Fix |
|---------|-----|
| `GEMINI_API_KEY is required` at startup | Key is missing from `.env` or not exported |
| Streaming cuts off mid-response | Gemini has higher reliability; try switching from a smaller model |
| Lite model errors on JSON output | Some models don't follow JSON format well for filter generation; use `gpt-4.1-mini` or `gemini-3-flash-preview` |
| `model-info` shows old provider | API hasn't been restarted yet |

---

## Related

- [Reference: Environment Variables](reference-environment-variables.md)
- [Explanation: Orchestration Pipeline](explanation-orchestration.md)
- [How-To: Monitor Usage](howto-monitor-usage.md)
