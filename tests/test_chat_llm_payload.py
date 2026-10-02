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
        inference_tier="paid",
    )

    assert payload == {
        "query": "question",
        "assistant": "main",
        "thread_id": "user_1:session_1",
        "generation_id": None,
        "user_uploaded_context": "collected context",
        "instructions": None,
        "inference_tier": "paid",
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
        inference_tier="paid",
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
        inference_tier="paid",
    )

    assert payload["instructions"] is not None
    assert "brief" in payload["instructions"]
    assert "professional" in payload["instructions"]


@pytest.mark.asyncio
async def test_llm_payload_carries_free_inference_tier(monkeypatch):
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
        inference_tier="free",
    )

    assert payload["inference_tier"] == "free"


@pytest.mark.asyncio
async def test_llm_payload_carries_paid_inference_tier(monkeypatch):
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
        inference_tier="paid",
    )

    assert payload["inference_tier"] == "paid"


def test_resolve_inference_tier_from_active_pool_subscription():
    assert ChatService.resolve_inference_tier({"kind": "pool"}) == "paid"


def test_resolve_inference_tier_from_active_daily_pass():
    assert ChatService.resolve_inference_tier({"kind": "daily_pass", "consumed": {}}) == "paid"


def test_resolve_inference_tier_from_signup_bonus_is_free():
    assert ChatService.resolve_inference_tier({"kind": "signup_bonus"}) == "free"


def test_resolve_inference_tier_from_daily_promo_quota_is_free():
    assert ChatService.resolve_inference_tier({"kind": "daily_promo"}) == "free"


def test_resolve_inference_tier_defaults_to_free_when_nothing_was_charged():
    """refund_info is None for an unlimited-promo charge (nothing billed) and
    for any path with no active subscription or daily pass -- both are free."""
    assert ChatService.resolve_inference_tier(None) == "free"
