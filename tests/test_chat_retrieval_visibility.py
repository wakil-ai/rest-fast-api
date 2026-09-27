import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from core.config import settings
from services.chat_service import ChatService
from utils.streaming import _format_sse_message


def event(kind, data, seq=1):
    return {"schema_version": 2, "type": kind, "run_id": "run-1",
            "revision": 0, "attempt_id": "attempt-1", "seq": seq,
            "emitted_at": "2026-09-27T00:00:00Z", "data": data}


ROUTE = {"phase": "effective", "assistant": "court", "task": "case_research",
         "targets": ["civil_cases", "legislation"], "court_route": "civil",
         "legal_intent": "general_legal"}


def sources(source_id="law-1", phase="retrieved", status="success"):
    return {"phase": phase, "sources": [
        {"source_id": source_id, "title": "Fuqarolik kodeksi", "target": "legislation",
         "url": "https://lex.uz/docs/111#222", "authority": "official",
         "temporal_status": "unknown", "selection_reason": "baseline"}
    ], "searches": [{"target": "legislation", "status": status,
                     "candidate_count": 1}], "selected_ids": [source_id],
       "excluded_ids": [], "truncated": False}


async def run_stream(events, enabled=True, endpoint="/api/v1/chat/ask/stream", opt_in=True):
    service = ChatService.__new__(ChatService)
    service.build_llm_inference_payload = AsyncMock(return_value={"query": "q"})
    service.rate_limit_service = MagicMock()
    service.rate_limit_service.refund_credits = AsyncMock()
    service.upload_final_answer_docx = AsyncMock(return_value=[])
    service._persist_assistant_message_safe = AsyncMock()
    captured = {}

    async def stream(path, payload):
        captured.update(path=path, payload=payload)
        for item in events:
            yield item

    with patch.object(settings, "LLM_STREAM_V2_ENABLED", enabled, create=True), patch(
        "services.chat_service.get_llm_service_client", return_value=MagicMock(stream_json=stream)
    ):
        out = [item async for item in service.astream_orchestrated_chat(
            user_id="u", session_id="s", message_id="msg-1", query="q", file_ids=None,
            assistant="main", started_at=0, credit_cost=3, refund_info={"kind": "test"},
            stream_endpoint=endpoint,
            include_retrieval_metadata=opt_in,
        )]
    return out, service, captured


@pytest.mark.parametrize("endpoint", ["/api/v1/chat/ask/stream", "/api/v1/chat/agent/stream"])
async def test_v2_stream_preserves_answer_attachments_route_sources_and_history(endpoint):
    out, service, captured = await run_stream([
        event("metadata", {"selected_assistant": "court", "resumable": False}),
        event("route", ROUTE, 2), event("sources", sources(), 3),
        event("chunk", {"chunk": "Javob "}, 4), event("chunk", {"chunk": "matni"}, 5),
        event("attachments", {"attachments": [{"name": "contract", "url": "https://example.org/a.docx"}]}, 6),
        event("end", {"status": "generated", "usage": {"model_calls": 2}}, 7),
    ], endpoint=endpoint)
    assert captured["payload"]["stream_protocol"] == 2
    assert captured["path"] == endpoint
    assert [x["chunk"] for x in out if x["type"] == "chunk"] == ["Javob ", "matni"]
    assert next(x for x in out if x["type"] == "route")["targets"] == ROUTE["targets"]
    assert next(x for x in out if x["type"] == "attachments")["attachments"][0]["name"] == "contract"
    persisted = service._persist_assistant_message_safe.await_args.kwargs
    assert persisted["answer"] == "Javob matni"
    assert persisted["metadata"]["retrieval_route"]["court_route"] == "civil"
    assert persisted["metadata"]["retrieval_sources"]["sources"][0]["source_id"] == "law-1"
    assert persisted["metadata"]["generation_status"] == "generated"
    assert service.upload_final_answer_docx.await_args.kwargs["classified_legal_intent"] == "general_legal"
    assert out[-1]["type"] == "end" and out[-1]["message_id"] == "msg-1"
    service.rate_limit_service.refund_credits.assert_not_awaited()


async def test_flag_off_keeps_legacy_request_and_stream():
    out, service, captured = await run_stream([
        {"type": "chunk", "chunk": "legacy answer"}, {"type": "end"}
    ], enabled=False)
    assert "stream_protocol" not in captured["payload"]
    assert service._persist_assistant_message_safe.await_args.kwargs["answer"] == "legacy answer"
    assert out[-1]["type"] == "end"


@pytest.mark.parametrize("text", ["", "partial answer"])
async def test_v2_error_preserves_refund_policy_and_never_saves_success(text):
    events = [event("chunk", {"chunk": text})] if text else []
    events += [event("error", {"code": "DEPENDENCY_UNAVAILABLE", "message": "Search unavailable", "retryable": True}, 2)]
    out, service, _ = await run_stream(events)
    assert out[-1]["type"] == "error"
    assert out[-1]["error"] == "Search unavailable"
    service._persist_assistant_message_safe.assert_not_awaited()
    assert service.rate_limit_service.refund_credits.await_count == (0 if text else 1)


@pytest.mark.parametrize("status", ["failed", "cancelled", "superseded", "awaiting_input"])
async def test_nonfinal_v2_end_is_not_persisted_as_success(status):
    out, service, _ = await run_stream([
        event("chunk", {"chunk": "unfinished"}), event("end", {"status": status}, 2)
    ])
    assert out[-1]["type"] == "error"
    service._persist_assistant_message_safe.assert_not_awaited()
    service.rate_limit_service.refund_credits.assert_not_awaited()


async def test_tool_sources_merge_without_losing_prior_sources_or_outage():
    initial = sources(status="partial")
    initial["searches"].append({"target": "civil_cases", "status": "unavailable", "candidate_count": 0})
    out, service, _ = await run_stream([
        event("sources", initial), event("sources", sources("law-2", "tool_update"), 2),
        event("chunk", {"chunk": "answer"}, 3), event("end", {"status": "generated"}, 4),
    ])
    snapshot = service._persist_assistant_message_safe.await_args.kwargs["metadata"]["retrieval_sources"]
    assert {s["source_id"] for s in snapshot["sources"]} == {"law-1", "law-2"}
    assert any(s["status"] == "unavailable" for s in snapshot["searches"])


async def test_visibility_drops_private_fields_and_unsafe_source_urls():
    data = sources()
    data["sources"][0].update(url="javascript:alert(1)", text="PRIVATE PASSAGE", record_id="internal")
    data["searches"][0]["errors"] = [{"message": "private backend details", "code": "DEPENDENCY_UNAVAILABLE"}]
    out, service, _ = await run_stream([
        event("sources", data), event("chunk", {"chunk": "answer"}, 2),
        event("end", {"status": "generated"}, 3),
    ])
    snapshot = service._persist_assistant_message_safe.await_args.kwargs["metadata"]["retrieval_sources"]
    assert snapshot["sources"][0]["url"] is None
    for serialized in (json.dumps(out), json.dumps(snapshot)):
        assert "PRIVATE PASSAGE" not in serialized
        assert "private backend details" not in serialized


@pytest.mark.parametrize("kind", ["route", "sources"])
def test_visibility_events_are_not_wrapped_as_debug(kind):
    item = {"type": kind, "targets": []}
    assert json.loads(_format_sse_message(item)[6:].strip()) == item


def test_v2_rollout_defaults_off():
    assert type(settings).model_fields["LLM_STREAM_V2_ENABLED"].default is False


async def test_older_client_stays_on_legacy_protocol_when_server_flag_is_on():
    _, _, captured = await run_stream([
        {"type": "chunk", "chunk": "answer"}, {"type": "end"}
    ], enabled=True, opt_in=False)
    assert "stream_protocol" not in captured["payload"]


def test_public_request_models_default_visibility_off():
    from models.chat import AgenticRAGRequest, ChatRequest

    for model in (AgenticRAGRequest, ChatRequest):
        request = model(user_id="u", session_id="s", query="q")
        assert request.include_retrieval_metadata is False
        assert model(user_id="u", session_id="s", query="q", include_retrieval_metadata=True).include_retrieval_metadata is True
