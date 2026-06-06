# Milvus Filter Expression Reference

Milvus uses a SQL-like expression language to filter documents before or during vector search. The WakilAI API generates these filters automatically via `MilvusQueryAgent`, but you can also pass them directly in API requests and understand what they mean in logs.

**Source:** [app/utils/milvus_expr.py](../app/utils/milvus_expr.py), [app/db/milvus_handler.py](../app/db/milvus_handler.py), [app/orchestration/agents/milvus_agent.py](../app/orchestration/agents/milvus_agent.py)

---

## Schema Fields Available for Filtering

Every document in every Milvus collection has these metadata fields:

| Field | Type | Description |
|-------|------|-------------|
| `source` | VARCHAR | Document source (e.g. `lex.uz`, `soliq.uz`, `sud.uz`) |
| `law` | VARCHAR | Law or regulation name (e.g. `Mehnat Kodeksi`) |
| `article` | INT64 | Article number within the law |
| `date` | VARCHAR | Document date (ISO format: `YYYY-MM-DD`) |
| `text` | VARCHAR | Document chunk text (max 65,535 chars) |
| `hierarchy_path` | VARCHAR | Hierarchical path within the legal corpus |

Project files additionally have:

| Field | Type | Description |
|-------|------|-------------|
| `project_id` | VARCHAR | Legal project workspace ID |
| `user_id` | VARCHAR | Owner user ID |

---

## Expression Syntax

### Comparison operators

```
article == 12
article != 0
article > 100
article >= 50
article < 200
article <= 150
date > "2020-01-01"
source == "lex.uz"
```

### IN / NOT IN

```
article in [12, 13, 14]
article not in [0, 999]
source in ["lex.uz", "sud.uz"]
```

### LIKE (prefix matching on VARCHAR)

```
law like "Mehnat%"
source like "lex%"
```

### Boolean operators

```
article == 12 and source == "lex.uz"
article in [5, 6, 7] or source == "soliq.uz"
not (article == 0)
```

### NULL / existence check

```
article != null
source != ""
```

---

## Common Filter Patterns

### Filter by article number

```
article == 128
```

Retrieves only documents from article 128 (useful for targeted legal queries like "What does Article 128 say?").

### Filter by multiple articles

```
article in [127, 128, 129]
```

### Filter by document source

```
source == "lex.uz"
```

All documents from the national legislation portal.

### Filter by source and article

```
source == "lex.uz" and article == 45
```

### Filter by date range

```
date >= "2022-01-01" and date <= "2023-12-31"
```

Retrieves documents enacted or updated between 2022 and 2023.

### Filter project files for a specific user

```
project_id == "abc123" and user_id == "user_456"
```

This filter is always applied automatically when `project_id` is present in the chat request.

### Filter by law name (partial match)

```
law like "Soliq kodeksi%"
```

### No filter (retrieve from entire collection)

When the query doesn't imply specific articles or sources, the filter is omitted and all documents in the collection are candidates.

---

## How Filters Are Generated

The `MilvusQueryAgent` (defined in [app/orchestration/agents/milvus_agent.py](../app/orchestration/agents/milvus_agent.py)) uses the lite LLM (`DEFAULT_LITE_MODEL`) to generate a filter expression from the rewritten query.

The agent is prompted with:
- The rewritten user query
- The available field names and their types
- Example filters for context

The output goes through `normalize_milvus_filter_llm_output()` in [app/utils/milvus_expr.py](../app/utils/milvus_expr.py), which:
1. Strips markdown code fences (` ``` `)
2. Removes prefixes like `filter:` or `expr:`
3. Extracts expressions from JSON wrappers (`{"filter": "..."}`)
4. Returns `None` for empty outputs (no filter applied)

If the generated expression is invalid, Milvus will reject the search; the handler falls back to unfiltered dense search.

---

## VARCHAR Length Limits

Milvus has a 65,535-character limit on VARCHAR fields. The `text` and `hierarchy_path` fields are capped at this limit.

When inserting documents:
- `truncate_milvus_varchar(text)` — truncates a single string to the limit (logs a warning)
- `split_text_for_milvus_varchar(text, max_len=65535, overlap=500)` — splits oversized text into overlapping chunks

---

## Debugging Filters

To see what filter was generated for a request, enable Langfuse tracing:

```env
LANGFUSE_TRACING_ENABLED=true
```

Langfuse records the filter expression as a span attribute on the `milvus_filter_generation` trace.

Alternatively, set `DEBUG=true` and check the API logs — filter expressions are logged at the `INFO` level before each hybrid search.

---

## Related

- [Explanation: Hybrid Vector Search](explanation-hybrid-search.md)
- [Reference: LangGraph State & Nodes](reference-langgraph-state.md)
- [How-To: Monitor Usage](howto-monitor-usage.md)
