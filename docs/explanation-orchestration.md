# How LangGraph Orchestration Works

This document explains the design of the WakilAI retrieval pipeline — what each stage does, why it exists, and what happens when things go wrong.

For a field-by-field reference, see [Reference: LangGraph State & Nodes](reference-langgraph-state.md).

---

## The problem this design solves

Simple RAG (retrieve → generate) breaks for legal questions in several ways:

1. **Ambiguous queries.** "What happens if I don't pay taxes?" could mean the tax penalty code, a civil court case, or a criminal prosecution. A single vector search query retrieves documents from the wrong domain.

2. **Specialized corpora.** Tax law lives in a different Milvus collection from administrative court rulings. The same pipeline can't serve both without routing.

3. **Insufficient corpus.** Uzbek legal databases are not exhaustive. A Gemini-class model with only retrieved context will hallucinate when the answer isn't in Milvus. The system needs a fallback.

4. **Stateful conversations.** Users follow up: "What is the penalty?" after "Tell me about Article 128." Without conversation memory, the follow-up query retrieves the wrong documents.

5. **Criminal cases are structured differently.** Criminal precedent lives in a Neo4j graph of case→section→person relationships, not in a flat document store.

The LangGraph pipeline handles all of these by decomposing the flow into discrete, independently testable nodes with conditional routing between them.

---

## The pipeline at a glance

```
START
  │
  ▼
[load_file_and_project_context]  ← load user uploads + project files from Milvus
  │
  ▼
[ingest_payload]  ← wrap query as HumanMessage for LangChain thread
  │
  ▼
[load_long_term_memory]  ← fetch user's persistent memories from LangGraph store
  │
  ▼
[rewrite_query]  ← lite LLM rewrites query using history + context
  │
  ▼
[recognize_intent]  ← lite LLM: classify legal domain (contract/tax/court/…)
  │
  ▼
[route_court]  ← lite LLM: determine court type if applicable
  │
  ├─── criminal_court ──────────────────────────────────────────────────┐
  │                                                                      ▼
  │                                                    [criminal_case_retrieval_subgraph]
  │                                                     Neo4j + Mongo case graph search
  │                                                                      │
  ▼ (all other assistants)                                               │
[retrieve_documents] ◄─────────────────────────────────────────────────┘
  Milvus hybrid search on the resolved collection
  │
  ├─── deep_research=true + TAVILY_API_KEY set ──────►[evaluate_context]
  │                                                    lite LLM: is context sufficient?
  │                                                         │
  │                                              ┌──────────┴──────────┐
  │                                           Yes │                   No│
  │                                              ▼                     ▼
  │                                    [generate_final_answer]  [web_search_fallback]
  │                                                                     │
  ▼ (deep_research=false)                                               ▼
[generate_final_answer] ◄────────────────────────────────────────────┘
  generation LLM streams final answer via Redis-checkpointed thread
  │
  ▼
END
```

---

## Stage-by-stage breakdown

### Stage 1 — Load context before the query enters the graph

Before any LLM call, the pipeline loads everything the user attached to this turn:
- **Uploaded files:** fetched from MongoDB (OCR text or GCS blob), capped at `FILE_CONTENT_TOKEN_LIMIT`.
- **Project files:** queried from Milvus `project_files` collection, filtered by `project_id` + `user_id`.

This happens first because later nodes (rewrite, intent, generation) all need access to this context.

### Stage 2 — Query rewrite

The user's raw query is rarely optimal for vector search. "What are my rights?" is too vague. "Is the landlord allowed to enter without notice?" is better, but the rewriter might expand it to "rights of a tenant regarding landlord entry under the Civil Code of Uzbekistan."

The rewriter uses the **lite model** (`DEFAULT_LITE_MODEL`) — fast and cheap — with:
- The raw query
- Last N conversation turns (from the Redis-checkpointed thread)
- Uploaded file context (so a question about "this contract" uses contract text as context)

The rewritten query is stored in `rewritten_query` and used for all downstream retrieval.

### Stage 3 — Intent and court routing

Two separate lite LLM calls classify the request:

**Intent recognition** (`recognize_intent`) runs only for `contract_analyzer` requests. It classifies the legal intent (e.g., "risk analysis", "party obligations") to select the right retrieval sub-mode.

**Court routing** (`route_court`) runs only for `court` and `administrative_court` requests. Uzbek courts are separate legal systems with separate document corpora:

| Court type | Collection |
|-----------|------------|
| Administrative | `mamuriy_sud_all` |
| Civil | `civil_court` |
| Economic | `economic_court` |
| Criminal | `criminal_court` (→ Neo4j subgraph) |

The router reads the query and outputs the specific court. If the query is about a criminal case, it sets `selected_assistant = "criminal_court"`, triggering the Neo4j subgraph branch.

### Stage 4 — Criminal case subgraph (conditional)

Criminal case retrieval is fundamentally different from Milvus search:

- Cases are structured as a graph: `Case → HAS_SECTION → LegalSection`, `Case → INVOLVES_PERSON → Person`, `Case → TRIED_AT → Court`.
- Relevant cases are found via vector similarity on case summaries (Neo4j vector index).
- Full case documents are then fetched from MongoDB (`criminal` database).
- Mode classification (0–8) determines which prompt sections to activate in the 16-part criminal prompt template.

The output is formatted markdown in `criminal_case_context`, which is later injected alongside Milvus results.

### Stage 5 — Document retrieval (Milvus)

Every path eventually reaches `retrieve_documents`, which:
1. Calls the resolved assistant's `.retrieve()` method.
2. The assistant generates a Milvus filter via `MilvusQueryAgent` (lite LLM call).
3. Runs hybrid search: `alpha × dense_score + (1 - alpha) × sparse_BM25_score`.
4. Formats results with source metadata into `retrieval_context`.

For `contract_analyzer`: additionally generates a DOCX attachment if a matching template is found above `CONTRACT_ATTACHMENT_MIN_SIMILARITY`.

### Stage 6 — Context evaluation + web search (conditional)

When `deep_research=True` and `TAVILY_API_KEY` is set:

1. A lite LLM call evaluates whether `retrieval_context` is sufficient to answer the question.
2. If not, a Tavily web search is run on the rewritten query.
3. Web results are merged into `retrieval_context` before generation.

This prevents hallucination on queries about recent events, amendments, or niche topics not in the corpus.

### Stage 7 — Final answer generation

The generation LLM (`DEFAULT_CHAT_MODEL`, default: Gemini) receives:
- A system prompt (assistant-specific, loaded from `src/orchestration/prompts/`)
- The retrieval context
- The user's question + rewritten query
- Uploaded file and project file context

Generation runs through `LangChain.astream_turn()`, which uses the Redis-checkpointed thread for conversation memory. Chunks are streamed via SSE.

After generation:
- `retrieval_context` and `criminal_case_context` are cleared from the checkpoint (they're large, transient, and not needed for follow-up turns).
- The answer is appended as an `AIMessage` to the thread.

---

## Trade-offs in this design

### Why LangGraph over a simple function pipeline?

- **State machine semantics** make the conditional branching (criminal subgraph, web fallback) explicit and testable.
- **Redis checkpointing** gives every session a persistent conversation thread at zero extra code.
- **Async execution** of all nodes allows concurrent file loading and Milvus searches where possible.

The cost: more indirection. Debugging requires tracing through node names and state diffs rather than a linear call stack.

### Why multiple lite LLM calls?

Query rewrite, intent, court routing, filter generation, and context evaluation are all separate lite LLM calls — typically gpt-4.1-mini at low cost. This adds ~300–500 ms latency per call but produces significantly better retrieval precision than a single-pass approach.

Alternative considered: a single "routing" LLM call that classifies everything. Rejected because the outputs (rewrite, intent, court, filter) have different formats and failure modes that are cleaner to handle separately.

### Why clear retrieval context from checkpoints?

A `RetrievalRewriteState` with 10 documents × 500 tokens each = ~5,000 tokens stored in Redis per turn. Over 100 turns, that's 500,000 tokens in the checkpoint — approaching Redis memory limits. Clearing after generation keeps the checkpoint to conversation messages only.

---

## What breaks and why

| Symptom | Cause |
|---------|-------|
| `NotImplementedError` on first message | Redis is plain Redis, not Redis Stack. `AsyncRedisSaver` needs RediSearch + RedisJSON. |
| Empty retrieval context | Milvus unreachable, wrong `MILVUS_URI`, or the collection is empty. |
| Wrong assistant routing | Query too ambiguous; inspect the `selected_assistant` field in Langfuse traces. |
| Web search never fires | `TAVILY_API_KEY` not set, or `deep_research=false` on the request. |
| Criminal cases not retrieved | `NEO4J_URI` not set or `CRIMINAL_GRAPH_RETRIEVAL_ENABLED=false`. |

---

## Related

- [Reference: LangGraph State & Nodes](reference-langgraph-state.md)
- [Reference: Milvus Filter Syntax](reference-milvus-filters.md)
- [Explanation: Hybrid Vector Search](explanation-hybrid-search.md)
- [Explanation: Assistant Routing Strategy](explanation-assistant-routing.md)
- [Tutorial: Stream a Deep-Research Answer](tutorial-streaming.md)
