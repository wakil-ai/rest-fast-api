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
        "generation_id": None,
        "user_uploaded_context": "collected context",
        "instructions": None,
    }


@pytest.mark.asyncio
async def test_llm_payload_forwards_generation_id(monkeypatch):
    service = ChatService.__new__(ChatService)

    async def fake_collect_user_uploaded_context(**kwargs):
        return ""

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
        generation_id="message_1",
    )

    assert payload["generation_id"] == "message_1"


@pytest.mark.asyncio
async def test_llm_payload_includes_ai_config_instructions(monkeypatch):
    service = ChatService.__new__(ChatService)

    async def fake_collect_user_uploaded_context(**kwargs):
        return ""

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
        response_style="brief",
        explanation_tone="professional",
    )

    assert payload["instructions"] is not None
    assert "brief" in payload["instructions"]
    assert "professional" in payload["instructions"]
