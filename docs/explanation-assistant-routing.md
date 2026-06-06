# Assistant Routing Strategy

How a chat request goes from `assistant: "court"` to a specific court type, Milvus collection, and prompt template.

---

## The routing problem

WakilAI serves five legal domains with distinct corpora and prompt strategies:

| Domain | What it covers |
|--------|----------------|
| General law (`main`) | Broad Uzbek legislation (LexUZ corpus) |
| Tax law (`tax`) | Soliq.uz corpus, fiscal regulations |
| Courts (`court`) | Separate systems: administrative, civil, economic, criminal |
| Contracts (`contract_analyzer`) | Contract review, risk analysis, template generation |
| Criminal cases (`criminal_court`) | Case-law via Neo4j graph + Mongo |

A single pipeline cannot serve all of these with the same retrieval parameters. Each domain has a different Milvus collection, different top-K, different alpha, and a different system prompt. The routing system maps an incoming request to the right configuration.

---

## Step 1 — Resolve the requested assistant

**File:** [src/orchestration/retrieval.py](../src/orchestration/retrieval.py) — `resolve_assistant()`

When a request arrives, `assistant_name` in the state is the client-provided value (e.g. `"court"`, `"soliq"`, `"deepresearch"`). The resolver normalizes it:

```python
def resolve_assistant(state) -> str:
    raw = (
        state.get("selected_assistant")
        or state.get("assistant_name")
        or state.get("collection_name")
        or "main"
    )
    return AssistantConfig.validate_assistant_or_default(str(raw))
```

`AssistantConfig.validate_assistant_or_default()` handles aliases:

| Client sends | Resolved to |
|-------------|------------|
| `deepresearch` / `deep_research` | `main` |
| `soliq` | `tax` |
| `mamuriy_sud` | `administrative_court` |
| `shartnoma` | `contract_analyzer` |
| `court` / `sud` | `court` |
| `unknown_value` | `main` (default fallback) |

---

## Step 2 — Intent recognition (contract only)

For `contract_analyzer` requests, the `recognize_intent` node classifies the specific legal intent using the lite LLM:

- `contract_risk_analysis` — identify risks in a contract
- `contract_template` — generate or find a template
- `contract_party_obligations` — extract obligations of parties
- `contract_term_explanation` — explain a specific clause

The intent is stored in `legal_intent` and influences which Milvus sub-filter and prompt variation is used.

---

## Step 3 — Court routing (court assistants only)

For `court` and `administrative_court` requests, the `route_court` node determines the specific court system.

**Trigger condition:** `assistant_name in {"court", "administrative_court"}`

The `CourtClassifier` uses the lite LLM to classify the query into one of:

| Output `selected_assistant` | Court system | Milvus collection |
|----------------------------|-------------|-------------------|
| `administrative_court` | Administrative courts | `mamuriy_sud_all` |
| `civil_court` | Civil courts | `civil_court` |
| `economic_court` | Economic / commercial courts | `economic_court` |
| `criminal_court` | Criminal courts | `criminal_court` + Neo4j |

The classifier also sets `court_route_tag` (a sub-type string) used for further collection filtering within administrative court cases.

---

## Step 4 — Assistant-specific retrieval

After routing, `retrieve_documents` dispatches to the resolved assistant's retriever:

**`main` (general law)**
- Collection: `lexuz`
- Top-K: 10 (global default)
- Alpha: 0.8
- Prompt: `system_prompt.md`

**`tax`**
- Collection: `soliq`
- Top-K: 10
- Alpha: 0.8
- Prompt: `soliq_system_prompt.md`

**`contract_analyzer`**
- Collection: `shartnoma` (primary) + `lexuz` (secondary, `ADDITIONAL_TOP_K=3`)
- Top-K: 10 + 3
- Generates DOCX attachment if a matching template scores above `CONTRACT_ATTACHMENT_MIN_SIMILARITY=0.6`
- Prompt: `contract_risk_analysis.md` or variant based on `legal_intent`

**`administrative_court`**
- Collection: `mamuriy_sud_all` (filtered by `court_route_tag`)
- Prompt: varies by `court_route_tag` (administrative, civil, economic subdivisions)

**`civil_court` / `economic_court`**
- Collections: `civil_court`, `economic_court`
- Prompts: `civil_court.md`, `economic_court.md`

**`criminal_court`**
- Primary: Neo4j graph traversal (case → section → person)
- Secondary: Milvus `criminal_court` collection
- Prompt: 16-part modular criminal prompt (composed by `CriminalPromptComposer` based on `criminal_modes`)

---

## How the Milvus filter is generated

Each assistant retriever calls `MilvusQueryAgent.generate_filter()`:

1. The lite LLM receives the rewritten query and available filter fields (`article`, `source`, `date`, `law`).
2. It outputs a Milvus filter expression or empty string.
3. The expression is normalized (`normalize_milvus_filter_llm_output`) and passed to the hybrid search.

Example: for "What does Article 128 of the Labor Code say?", the agent generates:

```
article == 128 and law like "Mehnat Kodeksi%"
```

This dramatically reduces the search space, improving both speed and precision.

---

## Prompt selection

Each assistant has a corresponding prompt template in `src/orchestration/prompts/`:

```
prompts/
├── system_prompt.md                    # main assistant
├── soliq_system_prompt.md              # tax
├── court_classify_prompt.md            # court routing (not answer prompt)
├── civil_court.md                      # civil court answer
├── economic_court.md                   # economic court answer
├── administrative_court.md             # administrative court answer
├── contract_risk_analysis.md           # contract analyzer
├── criminal_v2/                        # 16 modular parts for criminal
│   ├── 00_intro.md
│   ├── 01_case_summary.md
│   └── ... (parts 02–15)
└── ...
```

The `PromptRegistry` loads all templates at startup. `get_assistant_prompt(assistant_name)` returns the right `PromptTemplate`.

For criminal court, `CriminalPromptComposer` assembles a prompt from the 16 parts based on the `criminal_modes` list produced by the subgraph. Modes encode which analytical dimensions to activate (e.g. mode 3 = penalty analysis, mode 7 = mitigating circumstances).

---

## Trade-offs in this routing design

**Lite LLM routing vs. heuristic routing:**
Routing via LLM adds ~300 ms but handles ambiguous queries ("I was fired" could be labor law or administrative court). Heuristic routing (keyword matching) is faster but fails for paraphrased queries.

**Separate collections vs. one collection with filters:**
Separate collections give each assistant tuned indexing parameters and avoid cross-domain score bleeding. Cost: maintaining 9 collections instead of one. A single filtered collection would simplify ops but degrade retrieval quality because document embeddings from different legal domains would compete for the same embedding space.

**Hard-coded assistant names vs. fully dynamic configuration:**
Assistant names, collections, and credit costs are configurable via `ASSISTANTS` in `config.py`. Adding a new assistant requires a code change to wire a retriever, but collection names and credit costs can be changed via env vars.

---

## Related

- [Reference: LangGraph State & Nodes](reference-langgraph-state.md)
- [Reference: Milvus Filter Syntax](reference-milvus-filters.md)
- [Explanation: Orchestration Pipeline](explanation-orchestration.md)
- [Explanation: Hybrid Vector Search](explanation-hybrid-search.md)
