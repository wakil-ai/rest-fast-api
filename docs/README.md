# WakilAI Documentation Index

Use this index to navigate the documentation for the WakilAI REST API.

---

## New developers — start here

- [Onboarding guide](onboarding.md) — local setup, architecture, first tasks
- [Tutorial: Send your first legal question](tutorial-first-chat.md)
- [Tutorial: Stream a deep-research answer](tutorial-streaming.md)
- [Tutorial: Upload a file and chat about it](tutorial-file-upload.md)

---

## How-to guides

Practical instructions for specific tasks:

- [How to deploy to production](howto-deploy-production.md) — Docker Compose, Redis Stack, reverse proxy
- [How to switch LLM provider](howto-switch-llm-provider.md) — Gemini, OpenAI, Novita, local vLLM
- [How to configure payments](howto-configure-payments.md) — Payme/Paycom and Click setup
- [How to enable persistent memory](howto-enable-memory.md) — Mem0AI + Redis session memory
- [How to configure speech-to-text](howto-speech-to-text.md) — Azure and Google Cloud STT
- [How to monitor usage](howto-monitor-usage.md) — Langfuse tracing, token counts, credits

---

## Reference

Lookup tables and complete specs:

- [Environment variables](reference-environment-variables.md) — all 150+ variables with types and defaults
- [LangGraph state & nodes](reference-langgraph-state.md) — state fields, node signatures, edges
- [Milvus filter syntax](reference-milvus-filters.md) — filter expression reference with examples
- [Error codes](reference-error-codes.md) — HTTP errors and custom exceptions
- [API reference (all endpoints)](api-reference.md) — 200+ REST and WebSocket endpoints

---

## Explanation

Conceptual understanding of how things work:

- [API v2 Chat Ask end-to-end flow](explanation-v2-chat-ask-flow.md) — request lifecycle from auth and credits to persistence
- [How LangGraph orchestration works](explanation-orchestration.md) — 11-node pipeline with ASCII diagram
- [How hybrid vector search works](explanation-hybrid-search.md) — dense + sparse, alpha weighting
- [Assistant routing strategy](explanation-assistant-routing.md) — intent → court → collection selection
- [The credit system](explanation-credit-system.md) — per-assistant costs, subscriptions, promos

---

## Data & Storage

- [Database architecture & collections](database-architecture.md) — MongoDB schemas
- [File management & project workspaces](file-management.md) — upload pipeline, Milvus `project_files`

---

## Infrastructure & Deployment

- [Infrastructure overview](infrastructure.md)
- [Deployment notes (Milvus + Mongo)](deployment.md)

---

## Feature guides

- [DT/Birdarcha team integration](dt-team-integration.md)
- [Promo codes](promo-codes.md)
- [Rate limiting & credits](rate-limiting.md)
- [Referral tracking](referral-tracking.md)

---

## API changelog

- [Changelog (v3.0.0)](../CHANGELOG.md)
- [v3 release summary](updates-v3.md)
- [v2 route changes](updates-v2.md)
- [Development mode responses](development-mode.md)
- [Roadmap notes](plan.md)
