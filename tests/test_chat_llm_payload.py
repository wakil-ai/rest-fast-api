import pytest

from services.chat_service import ChatService


@pytest.mark.asyncio
async def test_llm_payload_sends_user_uploaded_context_only(monkeypatch):
    service = ChatService.__new__(ChatService)

    async def fake_collect_user_uploaded_context(**kwargs):
        return "collected context"

    monkeypatch.setattr(
        service,
        "collect_user_uploaded_context",
        fake_collect_user_uploaded_context,
    )

    payload = await service.build_llm_inference_payload(
        query="question",
        user_id="user_1",
        session_id="session_1",
        assistant="main",
        file_ids=["file_1"],
        project_id="project_1",
        file_context="inline",
    )

    assert payload == {
        "query": "question",
        "assistant": "main",
        "thread_id": "user_1:session_1",
        "user_uploaded_context": "collected context",
    }


@pytest.mark.asyncio
async def test_llm_payload_forwards_chat_model_only_when_chosen(monkeypatch):
    service = ChatService.__new__(ChatService)

    async def fake_collect_user_uploaded_context(**kwargs):
        return None

    monkeypatch.setattr(service, "collect_user_uploaded_context", fake_collect_user_uploaded_context)
    base = dict(query="q", user_id="u", session_id="s", assistant="main")

    chosen = await service.build_llm_inference_payload(**base, chat_model="glm-5.3-Lb6Zlz")
    assert chosen["chat_model"] == "glm-5.3-Lb6Zlz"
    assert "chat_model" not in await service.build_llm_inference_payload(**base)


def test_chat_requests_accept_chat_model():
    from models.chat import AgenticRAGRequest, ChatRequest

    assert ChatRequest(user_id="u", session_id="s", query="q", chat_model="gpt-6-luna").chat_model == "gpt-6-luna"
    assert ChatRequest(user_id="u", session_id="s", query="q").chat_model is None
    assert AgenticRAGRequest(user_id="u", session_id="s", query="q", chat_model="x").chat_model == "x"
