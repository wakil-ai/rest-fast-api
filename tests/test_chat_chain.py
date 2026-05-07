from unittest.mock import AsyncMock, patch

import pytest

from app.assistants.base import RetrievalResult
from app.chains.chat_chain import ChatChain
from app.core.config import settings


@pytest.fixture
def mock_dependencies():
    with (
        patch("app.chains.chat_chain.RetrievalService") as mock_ret,
        patch("app.chains.chat_chain.ChatMemoryService") as mock_mem,
        patch("app.chains.chat_chain.ChatGPT") as mock_gpt,
        patch("app.chains.chat_chain.Claude") as mock_claude,
        patch("app.chains.chat_chain.Novita") as mock_novita,
        patch("app.chains.chat_chain.MainAssistant") as mock_main_asst,
    ):
        # Setup Instances
        mock_ret_instance = mock_ret.return_value
        mock_mem_instance = mock_mem.return_value

        # Setup default async returns
        mock_mem_instance.search_memory = AsyncMock(return_value="")
        mock_ret_instance.retrieve_context = AsyncMock(
            return_value=("Valid Context", [])
        )

        # Mock main assistant's retrieve
        mock_main_instance = mock_main_asst.return_value
        mock_main_instance.retrieve = AsyncMock(
            return_value=RetrievalResult(context="Valid Context", attachments=[])
        )

        # LLM Mocks
        gpt_instance = mock_gpt.return_value
        gpt_instance.generate_response = AsyncMock(return_value="GPT Response")
        gpt_instance.count_tokens = AsyncMock(return_value=10)

        claude_instance = mock_claude.return_value
        claude_instance.generate_response = AsyncMock(return_value="Claude Response")
        claude_instance.count_tokens = AsyncMock(return_value=10)

        novita_instance = mock_novita.return_value
        novita_instance.generate_response = AsyncMock(return_value="Novita Response")
        novita_instance.count_tokens = AsyncMock(return_value=10)

        yield {
            "retrieval": mock_ret_instance,
            "memory": mock_mem_instance,
            "gpt": gpt_instance,
            "claude": claude_instance,
            "novita": novita_instance,
            "main_assistant": mock_main_instance,
            # Classes for checking factory logic
            "GPTClass": mock_gpt,
            "ClaudeClass": mock_claude,
            "NovitaClass": mock_novita,
        }


@pytest.fixture
def chat_chain(mock_dependencies):
    chain = ChatChain()
    chain.history_service.get_recent_messages = AsyncMock(return_value=[])
    return chain


def test_llm_factory_selection(chat_chain, mock_dependencies):
    """Test configured default model selection."""

    original_model = settings.DEFAULT_CHAT_MODEL
    try:
        settings.DEFAULT_CHAT_MODEL = "gpt-4"
        chat_chain._select_llm()
        mock_dependencies["GPTClass"].assert_called_with(model_name="gpt-4")

        settings.DEFAULT_CHAT_MODEL = "claude-3-opus"
        chat_chain._select_llm()
        mock_dependencies["ClaudeClass"].assert_called_with(model_name="claude-3-opus")

        settings.DEFAULT_CHAT_MODEL = "gemma-2b"
        chat_chain._select_llm()
        mock_dependencies["NovitaClass"].assert_called_with(model_name="gemma-2b")
    finally:
        settings.DEFAULT_CHAT_MODEL = original_model


@pytest.mark.asyncio
async def test_generate_answer_flow(chat_chain, mock_dependencies):
    """Test full answer generation flow."""
    user_id = "test_user"
    query = "test query"

    answer = await chat_chain.generate_answer(
        user_id=user_id, session_id="session_1", query=query, stream=False
    )

    assert answer

    # Check memory search
    mock_dependencies["memory"].search_memory.assert_awaited_with(user_id, query)

    # Check LLM generation
    mock_dependencies["gpt"].generate_response.assert_awaited()
    call_kwargs = mock_dependencies["gpt"].generate_response.call_args[1]
    assert "Valid Context" in call_kwargs["system_prompt"]


@pytest.mark.asyncio
async def test_streaming_response(chat_chain, mock_dependencies):
    """Test streaming response generator."""

    async def async_gen():
        yield "Part 1"
        yield "Part 2"

    mock_dependencies["gpt"].generate_response.return_value = async_gen()

    response_gen = await chat_chain.generate_answer(
        user_id="u1", session_id="session_1", query="q", stream=True
    )

    parts = []
    async for chunk in response_gen:
        if isinstance(chunk, str):
            parts.append(chunk)

    assert "".join(parts) == "Part 1Part 2"


@pytest.mark.asyncio
async def test_streaming_response_preserves_think_events(chat_chain, mock_dependencies):
    """Thinking events should stream separately and not be added to the answer."""

    async def async_gen():
        yield {"type": "think", "chunk": "Planning"}
        yield "Part 1"
        yield "Part 2"

    mock_dependencies["gpt"].generate_response.return_value = async_gen()

    response_gen = await chat_chain.generate_answer(
        user_id="u1", session_id="session_1", query="q", stream=True
    )

    think_chunks = []
    answer_parts = []

    async for chunk in response_gen:
        if isinstance(chunk, dict) and chunk.get("type") == "think":
            think_chunks.append(chunk["chunk"])
        if isinstance(chunk, str):
            answer_parts.append(chunk)

    assert think_chunks == ["Planning"]
    assert "".join(answer_parts) == "Part 1Part 2"


@pytest.mark.asyncio
async def test_get_session_history_text_formats_recent_messages(chat_chain):
    chat_chain.history_service.get_recent_messages = AsyncMock(
        return_value=[
            {
                "content": {
                    "query": "What is VAT?",
                    "response": "VAT is a value-added tax.",
                }
            },
            {
                "content": {
                    "query": "How is it calculated?",
                    "response": "It depends on the rate.",
                }
            },
        ]
    )

    formatted = await chat_chain._get_session_history_text("session_1")

    assert "1. User: What is VAT?" in formatted
    assert "Assistant: VAT is a value-added tax." in formatted
    assert "2. User: How is it calculated?" in formatted


@pytest.mark.asyncio
async def test_get_session_history_text_skips_incomplete_messages(chat_chain):
    chat_chain.history_service.get_recent_messages = AsyncMock(
        return_value=[
            {"content": {"query": "Can I appeal?", "response": ""}},
            {
                "content": {
                    "query": "What deadline applies?",
                    "response": "The filing window depends on the procedure.",
                }
            },
        ]
    )

    formatted = await chat_chain._get_session_history_text("session_1")

    assert "Can I appeal?" not in formatted
    assert "1. User: What deadline applies?" in formatted


@pytest.mark.asyncio
async def test_collect_file_context_falls_back_to_ocr_on_milvus_failure(chat_chain):
    file_id = "file-1"
    chat_chain.history_service.get_file_by_id = AsyncMock(
        return_value={
            "_id": file_id,
            "ocr_result": "OCR fallback content",
            "file_metadata": {
                "file_name": "contract.docx",
                "milvus_file_index": {"enabled": True},
            },
        }
    )
    chat_chain._retrieve_milvus_file_context = AsyncMock(
        side_effect=RuntimeError("milvus unavailable")
    )

    context = await chat_chain._collect_file_context(
        [file_id], user_id="user-1", query="What is penalty?"
    )

    assert "OCR fallback content" in context
    assert "USER FILE CONTEXT: contract.docx" in context


@pytest.mark.asyncio
async def test_retrieve_milvus_file_context_returns_empty_on_error(chat_chain):
    with patch(
        "app.chains.chat_chain.SiliconFlowEmbedding.embed_query",
        side_effect=RuntimeError("embed failed"),
    ):
        context = await chat_chain._retrieve_milvus_file_context(
            file_id="file-1",
            user_id="user-1",
            query="query",
            file_name="file.pdf",
        )

    assert context == ""
