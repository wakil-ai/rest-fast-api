# Tool-First Assistant Agents

## Summary
Refactor the chat agents so they do not pre-retrieve context and hand one static prompt to the LLM. Each assistant becomes a tool-first LangGraph/LangChain agent: it receives the user query plus lightweight turn state, reasons over available tools, calls retrieval/filter/context tools as needed, then produces the final answer according to that assistant’s prompt.

`ChatOrchestrator` stays a thin router. `/chat/ask` public behavior stays the same.

## Key Changes
- Convert `BaseAgent.ainvoke/astream` from “prepare all context first, then generate” into “build agent runtime with domain tools, then invoke/stream.”
- Keep loading small turn inputs before generation only where needed:
  - request metadata,
  - assistant name,
  - session/message ids,
  - optional file/project ids.
- Move history, memory, file context, project context, retrieval, court filters, and contract intent behind callable tools.
- Add a shared agent tool layer under `app/agents/common`:
  - `get_chat_history`
  - `search_memory`
  - `get_uploaded_file_context`
  - `get_project_context`
  - `search_legal_corpus`
  - `search_web` only when Tavily is configured
- Add domain tool sets per assistant:
  - `MainAgent`: legal corpus search, history, memory, file/project context.
  - `TaxAgent`: tax-source search with existing lex.uz/buxgalter/general strategy exposed as a tool.
  - `ContractAnalyzerAgent`: classify contract intent, search contract templates, search legal corpus, generate DOCX attachment after final answer when template intent is selected.
  - `CourtAgent`: classify court route, delegate to court-specific agent.
  - Court-specific agents: generate Milvus filter, search court cases with file-level expansion, search LexUZ legal corpus.
- Use the existing LangChain `create_agent` runtime/checkpointer, but pass each agent’s tool list and prompt instructions instead of prebuilt retrieved context.
- Add a balanced default tool budget of about 4-6 tool calls per answer through runtime config/recursion limits.
- Final answer evidence policy follows each assistant prompt. Add only a shared low-level instruction: use available tools for legal/court/tax/contract factual claims and avoid inventing unavailable sources.

## Behavior And Metadata
- Preserve `/chat/ask` request/response shape, streaming events, credits, persistence, DT disclaimer, assistant aliases, and contract attachments.
- Streaming still emits metadata first, chunks during generation, attachments when present, then persists final answer.
- Metadata should include tool/retrieval trace fields useful for debugging:
  - `tool_calls`
  - `retrieval_queries`
  - `retrieved_collections`
  - `selected_assistant`
  - existing `model`, `workflow`, `token_usage`, `attachments`.
- Existing `retrieve()` methods remain available but become tool implementation helpers instead of mandatory pre-generation steps.

## Test Plan
- Add tests that prove agents are tool-first:
  - `BaseAgent.ainvoke` does not call `retrieve_context` before model invocation.
  - agent runtime receives assistant-specific tools.
  - mocked model can call retrieval tools and final answer includes tool output.
- Add domain tests:
  - `MainAgent` exposes legal/history/memory/file/project tools.
  - `TaxAgent` tool uses current tax retrieval strategy.
  - `ContractAnalyzerAgent` calls intent classifier tool and creates DOCX only for template intent.
  - `CourtAgent` routes to the correct court-specific agent.
  - court-specific agents expose filter generation and court-case retrieval tools.
- Add streaming tests:
  - tool-call streaming still emits chunks to client.
  - final answer and metadata persist to DB.
  - attachments event and metadata are preserved.
- Run:
  - `.venv/bin/python -m compileall app/agents app/chains app/services app/orchestration app/main.py`
  - `.venv/bin/pytest tests -q`

## Assumptions
- We will implement the tool-first option, not the hybrid pre-retrieval option.
- Default tool budget is balanced: roughly 4-6 tool calls per answer.
- Assistant prompts remain the source of domain-specific answer policy.
- No public API field names change; user-facing requests can still say `assistant`.

## Never do
- Except lex.uz never expose other links (except if you got from web search)
- In code we have some constraints and formatting concepts for some asssitants need to follow on this too.