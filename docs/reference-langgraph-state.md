# LangGraph Orchestration — State & Node Reference

Complete reference for the retrieval graph defined in [app/orchestration/graph.py](../app/orchestration/graph.py). Every state field, node signature, and conditional edge is documented here.

For a conceptual explanation of why the graph is structured this way, see [Explanation: LangGraph Orchestration](explanation-orchestration.md).

---

## State Schema — `RetrievalRewriteState`

**File:** [app/orchestration/state.py](../app/orchestration/state.py)

`RetrievalRewriteState` is a `TypedDict` with `total=False` (all fields optional at construction; nodes fill them incrementally).

### Input Fields (set by caller before graph invocation)

| Field | Type | Description |
|-------|------|-------------|
| `query` | `str` | The user's raw question text |
| `user_id` | `str` | User identifier (used for memory namespace and logging) |
| `session_id` | `str` | Session identifier (maps to a Redis checkpoint thread) |
| `project_id` | `str` | Optional legal project workspace ID |
| `collection_name` | `str` | Milvus collection override (legacy; prefer `assistant_name`) |
| `assistant_name` | `str` | Requested assistant: `main`, `tax`, `court`, `contract_analyzer`, `criminal_court`, etc. |
| `deep_research` | `bool` | When `true`, enables context evaluation + Tavily web search fallback |
| `file_ids` | `list[str]` | File IDs uploaded to this turn; their text is loaded as context |
| `message_id` | `str` | Message ID assigned by the API before invocation |
| `file_context` | `str\|None` | Inline document text passed directly without a file ID |

### Fields Populated During Graph Execution

| Field | Set by node | Type | Description |
|-------|-------------|------|-------------|
| `messages` | `ingest_payload` | `list[BaseMessage]` | LangChain message history (append-only via `add_messages`) |
| `project_related_context` | `load_file_and_project_context` | `str\|None` | Text retrieved from Milvus `project_files` for the active project |
| `resolved_file_ids` | `load_file_and_project_context` | `list[str]` | File IDs successfully loaded and embedded |
| `resolved_project_id` | `load_file_and_project_context` | `str\|None` | Confirmed project ID after resolution |
| `long_term_memory` | `load_long_term_memory` | `str` | Formatted memories from LangGraph store (Mem0AI namespace) |
| `rewritten_query` | `rewrite_query` | `str` | Query rewritten for better retrieval (includes history + context signals) |
| `intent_domain` | `recognize_intent` | `str` | Broad legal domain (e.g. `contract`, `tax`) |
| `legal_intent` | `recognize_intent` | `str` | Fine-grained intent value (from `LegalIntent` enum) |
| `selected_assistant` | `recognize_intent` / `route_court` | `str` | Final resolved assistant name after routing |
| `court_route_tag` | `route_court` | `str\|None` | Court sub-type tag (e.g. `civil`, `economic`, `administrative`) |
| `milvus_filter` | (set by MilvusAgent) | `str` | Generated Milvus filter expression |
| `answer_prompt_template` | `retrieve_documents` | `Any` | Specialist prompt template to use for generation (overrides default) |
| `classified_legal_intent` | `retrieve_documents` | `str` | Post-retrieval intent classification |
| `attachments` | `retrieve_documents` | `list[dict]` | Contract DOCX or other file attachments to include in the response |
| `retrieval_context` | `retrieve_documents` | `str` | Formatted text from retrieved Milvus documents |
| `criminal_case_context` | `criminal_case_retrieval_subgraph` | `str` | Formatted criminal case results from Neo4j + Mongo |
| `criminal_modes` | `criminal_case_retrieval_subgraph` | `list[int]` | Mode indices (0–8) for `CriminalPromptComposer` |
| `context_evaluation_output` | `evaluate_context` | `dict` | Evaluation result from lite LLM (sufficiency score, gaps) |
| `web_search_output` | `web_search_fallback` | `dict` | Raw Tavily search results |
| `final_answer` | `generate_final_answer` | `str` | Complete generated answer text |

### Context Schema — `GraphContext`

Passed alongside the state as the runtime context.

| Field | Type | Description |
|-------|------|-------------|
| `user_id` | `str` | User ID made available to nodes that need it (e.g. `load_long_term_memory`) |

---

## Nodes

### 1. `load_file_and_project_context`

**Type:** Async  
**Input fields used:** `file_ids`, `project_id`, `user_id`  
**Output fields:** `file_context`, `project_related_context`, `resolved_file_ids`, `resolved_project_id`

Loads uploaded file text and project workspace documents:
- Fetches file records from MongoDB, reads OCR text or GCS content.
- Queries Milvus `project_files` collection filtered by `project_id` + `user_id`.
- Combines both into `file_context` passed to downstream nodes.

---

### 2. `ingest_payload`

**Type:** Sync  
**Input fields used:** `query`  
**Output fields:** `messages`

Wraps the user query as a `HumanMessage` and appends it to the message list. This seeds the LangChain message thread.

---

### 3. `load_long_term_memory`

**Type:** Sync (accesses LangGraph store — requires `runtime.store`)  
**Input fields used:** `user_id` (from `GraphContext`)  
**Output fields:** `long_term_memory`

Searches the LangGraph store under namespace `("legal_assistant", "users", {user_id}, "memories")` with `limit=5`. Returns formatted memory text. Raises `RuntimeError` if the store is unavailable (prevents silent omission of memory).

---

### 4. `rewrite_query`

**Type:** Async (lite LLM call)  
**Input fields used:** `query`, `messages` (history), `file_context`, `long_term_memory`  
**Output fields:** `rewritten_query`

Uses `DEFAULT_LITE_MODEL` to rewrite the user's query for better retrieval. Incorporates conversation history and any uploaded file context so the rewritten query is self-contained. Uses the `retrieval_query_rewrite` prompt template.

---

### 5. `recognize_intent`

**Type:** Async (lite LLM call, conditional)  
**Input fields used:** `rewritten_query`, `assistant_name`, `file_context`  
**Output fields:** `intent_domain`, `legal_intent`, `selected_assistant`

Only runs when the assistant is `contract_analyzer` or `contract`. Classifies the legal intent using `IntentClassifier` (defined in [app/orchestration/agents/intent_recognition.py](../app/orchestration/agents/intent_recognition.py)). For other assistants, the node is a no-op (returns `{}`).

---

### 6. `route_court`

**Type:** Async (lite LLM call, conditional)  
**Input fields used:** `rewritten_query`, `assistant_name`, `file_context`  
**Output fields:** `selected_assistant`, `court_route_tag`

Only runs when the assistant is `court` or `administrative_court`. Uses `CourtClassifier` to determine the specific court type (`civil_court`, `economic_court`, `administrative_court`, `criminal_court`). Sets `selected_assistant` to the routed court assistant and `court_route_tag` for collection/prompt selection.

---

### 7. `criminal_case_retrieval_subgraph` (conditional)

**Type:** Async (Neo4j + Mongo graph traversal)  
**Trigger:** `selected_assistant == "criminal_court"` after `route_court`  
**Input fields used:** `rewritten_query`, `query`, `messages`  
**Output fields:** `criminal_case_context`, `criminal_modes`

Builds and invokes a sub-LangGraph defined in `criminal_retrieval_langgraph.py`:
1. Classifies criminal modes (0–8) via `CriminalModeClassifier`.
2. Searches Neo4j vector index for relevant cases.
3. Fetches full case documents from MongoDB (`criminal` database).
4. Returns formatted markdown context and mode indices.

---

### 8. `retrieve_documents`

**Type:** Async (Milvus hybrid search)  
**Input fields used:** `rewritten_query`, `selected_assistant`, `court_route_tag`, `file_context`  
**Output fields:** `retrieval_context`, `answer_prompt_template`, `classified_legal_intent`, `attachments`

Dispatches to the correct assistant retriever via `retrieve_for_assistant()`:
- `main` → `MainAssistant.retrieve()` → `lexuz` collection
- `tax` → `TaxAssistant.retrieve()` → `soliq` collection
- `contract_analyzer` → `ContractAnalyzer.retrieve()` → `shartnoma` collection + attachment generation
- `court` → `CourtAgent._route()` → sub-agent based on court type
- `*_court` specialists → direct specialist agents

Each agent runs hybrid Milvus search (dense + sparse), generates a Milvus filter expression using `MilvusQueryAgent`, and formats the results into `retrieval_context`.

---

### 9. `evaluate_context` (conditional)

**Type:** Async (lite LLM call)  
**Trigger:** `service.web_search_enabled(state) == True`  
**Input fields used:** `query`, `retrieval_context`  
**Output fields:** `context_evaluation_output`

Uses the lite LLM to assess whether the retrieved context is sufficient to answer the question. Returns a dict with sufficiency score and gap descriptions. This gating prevents unnecessary Tavily API calls.

---

### 10. `web_search_fallback` (conditional)

**Type:** Async (Tavily API call)  
**Trigger:** `should_run_web_search()` returns `True` based on context evaluation  
**Input fields used:** `rewritten_query`, `retrieval_context`, `context_evaluation_output`  
**Output fields:** `web_search_output`, `retrieval_context` (merged)

Runs a Tavily web search for the rewritten query, then merges the web results into the existing `retrieval_context`. The merged context is passed to `generate_final_answer`.

---

### 11. `generate_final_answer`

**Type:** Async (generation LLM, streaming)  
**Input fields used:** all context fields, `query`, `rewritten_query`, `retrieval_context`, `criminal_case_context`, `file_context`, `project_related_context`, `selected_assistant`, `criminal_modes`  
**Output fields:** `final_answer`, `messages`, `retrieval_context` (cleared), `criminal_case_context` (cleared)

Builds the final system + user prompt and streams the answer via `LangChain.astream_turn()`:
- For `criminal_court`: uses `CriminalPromptComposer` with the criminal modes.
- For all others: uses the assistant-specific prompt template.
- Injects current date, retrieval context, and chat history hint.
- Streams via the Redis-checkpointed thread (preserves cross-turn memory).
- Clears `retrieval_context` and `criminal_case_context` from the checkpoint to reduce Redis storage.

---

## Conditional Edges

```
route_court
  ├── selected_assistant == "criminal_court"  →  criminal_case_retrieval_subgraph
  └── otherwise                               →  retrieve_documents

retrieve_documents
  ├── web_search_enabled(state) == True  →  evaluate_context
  └── otherwise                         →  generate_final_answer

evaluate_context
  ├── should_run_web_search(...) == True  →  web_search_fallback
  └── otherwise                          →  generate_final_answer
```

`web_search_enabled(state)` returns `True` when:
- `TAVILY_API_KEY` is set, AND
- The assistant supports web search, AND
- `deep_research=True` was requested (for the `agent/stream` endpoint)

---

## Full Edge List

```
START → load_file_and_project_context
load_file_and_project_context → ingest_payload
ingest_payload → load_long_term_memory
load_long_term_memory → rewrite_query
rewrite_query → recognize_intent
recognize_intent → route_court
route_court → [conditional] criminal_case_retrieval_subgraph | retrieve_documents
criminal_case_retrieval_subgraph → retrieve_documents
retrieve_documents → [conditional] evaluate_context | generate_final_answer
evaluate_context → [conditional] web_search_fallback | generate_final_answer
web_search_fallback → generate_final_answer
generate_final_answer → END
```

---

## Related

- [Explanation: How Orchestration Works](explanation-orchestration.md)
- [Explanation: Assistant Routing Strategy](explanation-assistant-routing.md)
- [Reference: Milvus Filter Syntax](reference-milvus-filters.md)
