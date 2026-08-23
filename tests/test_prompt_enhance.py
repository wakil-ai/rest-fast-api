import pytest

from core.exceptions import ChatGenerationException, QueryTooLongException
from models.chat import AssistantType, PromptEnhanceRequest
from services import chat_service as chat_service_module
from services.chat_service import ChatService


class _FakeLlmClient:
    def __init__(self, result: dict | Exception) -> None:
        self.result = result
        self.payload: dict | None = None

    async def enhance_prompt(self, payload: dict) -> dict:
        self.payload = payload
        if isinstance(self.result, Exception):
            raise self.result
        return self.result


def _patch_client(monkeypatch, result) -> _FakeLlmClient:
    client = _FakeLlmClient(result)
    monkeypatch.setattr(
        chat_service_module, "get_llm_service_client", lambda: client
    )
    return client


@pytest.mark.asyncio
async def test_enhance_forwards_canonical_assistant_and_returns_result(monkeypatch):
    service = ChatService.__new__(ChatService)
    client = _patch_client(
        monkeypatch,
        {
            "original": "qqs",
            "enhanced": "2026-yilda QQS stavkasi qancha?",
            "assistant": "tax",
            "changed": True,
        },
    )

    result = await service.handle_prompt_enhance(
        PromptEnhanceRequest(query="qqs", assistant=AssistantType.TAX, language="uz")
    )

    assert client.payload == {"query": "qqs", "assistant": "tax", "language": "uz"}
    assert result.enhanced == "2026-yilda QQS stavkasi qancha?"
    assert result.changed is True


@pytest.mark.asyncio
async def test_legacy_alias_is_canonicalized_before_forwarding(monkeypatch):
    service = ChatService.__new__(ChatService)
    client = _patch_client(
        monkeypatch,
        {"original": "d", "enhanced": "d", "assistant": "contract_analyzer", "changed": False},
    )

    await service.handle_prompt_enhance(
        PromptEnhanceRequest(query="d", assistant=AssistantType.CONTRACT_ANALYZER_LEGACY)
    )

    assert client.payload is not None
    assert client.payload["assistant"] == "contract_analyzer"


@pytest.mark.asyncio
async def test_enhancement_never_charges_credits(monkeypatch):
    """A helper button that costs a question's credit is a button nobody presses."""
    service = ChatService.__new__(ChatService)
    _patch_client(
        monkeypatch,
        {"original": "q", "enhanced": "q", "assistant": "main", "changed": False},
    )

    called = False

    async def fail_verify(*_args, **_kwargs):
        nonlocal called
        called = True

    monkeypatch.setattr(service, "verify_user_credits", fail_verify)

    await service.handle_prompt_enhance(PromptEnhanceRequest(query="q"))

    assert called is False


@pytest.mark.asyncio
async def test_missing_fields_fall_back_to_the_draft(monkeypatch):
    """The composer must never be emptied by a partial upstream response."""
    service = ChatService.__new__(ChatService)
    _patch_client(monkeypatch, {})

    result = await service.handle_prompt_enhance(PromptEnhanceRequest(query="soliq"))

    assert result.original == "soliq"
    assert result.enhanced == "soliq"
    assert result.assistant == "main"
    assert result.changed is False


@pytest.mark.asyncio
async def test_upstream_failure_becomes_chat_generation_exception(monkeypatch):
    service = ChatService.__new__(ChatService)
    _patch_client(monkeypatch, RuntimeError("inference down"))

    with pytest.raises(ChatGenerationException):
        await service.handle_prompt_enhance(PromptEnhanceRequest(query="soliq"))


@pytest.mark.asyncio
async def test_oversized_draft_is_rejected_before_any_upstream_call(monkeypatch):
    from core.config import settings

    service = ChatService.__new__(ChatService)
    client = _patch_client(monkeypatch, {})

    with pytest.raises(QueryTooLongException):
        await service.handle_prompt_enhance(
            PromptEnhanceRequest(query="a" * (settings.MAX_QUERY_LENGTH + 1))
        )

    assert client.payload is None
