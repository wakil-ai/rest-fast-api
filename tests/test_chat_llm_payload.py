import pytest

from app.services.chat_service import ChatService


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
