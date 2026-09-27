# Chat route and source visibility

**Status:** implemented behind a flag · **Date:** 2026-09-27

**Owner:** B2C API team

## Scope

Normal chat and deep research can consume inference V2 SSE internally. The public
API keeps flat `metadata`, `chunk`, `progress`, `attachments`, `error`, and `end`
events, and adds flat `route` and `sources` events. This requires both the server
flag and `include_retrieval_metadata=true` in the public streaming request. The
request field defaults to false: older mobile/web clients stay on inference V1
even with the server flag on. Sync chat, credits, auth and document collection
are unchanged.

The backend saves `retrieval_route`, `retrieval_sources`, `generation_status` and
the final `usage_ledger` in assistant message metadata. Sources from answer-tool
searches are merged into a bounded, complete snapshot. Passage text, internal
record IDs and raw dependency error messages are not exposed in source metadata.
Inline uploaded context retains the aggregate identity supplied by inference; this
does not create individual source records for every file.

## Public stream examples

```json
{"type":"route","phase":"effective","assistant":"court","task":"case_research","targets":["civil_cases","legislation"],"court_route":"civil","legal_intent":"general_legal"}
```

```json
{"type":"sources","phase":"retrieved","sources":[{"source_id":"law-1","target":"legislation","title":"Fuqarolik kodeksi","url":"https://lex.uz/docs/111","authority":"official","temporal_status":"unknown","selection_reason":"baseline"}],"searches":[{"target":"legislation","status":"success","candidate_count":1},{"target":"civil_cases","status":"unavailable","candidate_count":0}],"selected_ids":["law-1"],"excluded_ids":[],"truncated":false}
```

`sources` events are complete accumulated snapshots, including `tool_update`.
The UI distinguishes `empty`, `partial`, and `unavailable`; these are not equivalent.
Selected sources are answer context, not a claim that the answer cited every one.
The public payloads above are projections, not raw inference V2 envelopes.

V2 `data.chunk` becomes the existing public `chunk`. V2 stage progress keeps
`stage`, `state`, `outcome` and `iteration`. `end.status` is forwarded when available;
`budget_exhausted` is explicitly labelled. Failed/cancelled/superseded/paused runs
and V2 streams without a terminal event do not enter the success persistence path.
The existing no-text refund policy is preserved.

## Rollout and rollback

1. Deploy the API and companion frontend changes with `LLM_STREAM_V2_ENABLED=false`.
2. On inference, enable `RAG_EVENTS_V2_ENABLED=true`. Keep
   `RAG_RUN_RECORDS_ENABLED=false`, `RAG_OVERRIDES_ENABLED=false` and
   `RAG_CORRECTIONS_ENABLED=false`. No planner, reranker or evidence assembly flag
   is required. Do not enable durable runs: this caller does not supply their
   conversation-version or commit protocol.
3. Set `LLM_STREAM_V2_ENABLED=true` on rest-fast-api and restart/recreate its service.
   The new web client advertises `include_retrieval_metadata=true`. It cannot
   bypass the server flag, select the internal protocol or submit overrides.
4. Check ordinary chat, deep research, a file question, and a retrieval outage.
   Confirm streamed text equals saved history, sources survive reload, attachments
   remain available, and empty failures follow the existing refund policy.

Rollback: set `LLM_STREAM_V2_ENABLED=false` and restart rest-fast-api. Do that before
disabling inference V2. No migration or history deletion is needed. No automatic
retry with another protocol is performed, to avoid duplicate work or charges.

## Tests

Write regressions before implementation. Run:

```sh
PYTHONPATH=src python -m pytest tests/test_chat_retrieval_visibility.py tests/test_chat_stream_error_refund.py tests/test_chat_llm_payload.py tests/test_llm_service_client.py
```

Tests use fake inference streams and mocked persistence/refunds; no model or
database calls. The existing LLM client fakes now implement `text`/`is_success`,
which the production client already requires.

Route overrides, source editing, pause/resume, run snapshots and corrected-answer
commits are outside this change and remain disabled.
