from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.assistants.base import RetrievalResult
from app.chains.chat_chain import ChatChain


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
        mock_ret_instance.retrieve_context = AsyncMock(return_value=("Valid Context", []))

        # Mock main assistant's retrieve
        mock_main_instance = mock_main_asst.return_value
        mock_main_instance.retrieve = AsyncMock(
            return_value=RetrievalResult(context="Valid Context", attachments=[])
        )

        # LLM Mocks
        gpt_instance = mock_gpt.return_value
        gpt_instance.generate_response = AsyncMock(return_value="GPT Response")

        claude_instance = mock_claude.return_value
        claude_instance.generate_response = AsyncMock(return_value="Claude Response")

        novita_instance = mock_novita.return_value
        novita_instance.generate_response = AsyncMock(return_value="Novita Response")

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
    return ChatChain()


def test_llm_factory_selection(chat_chain, mock_dependencies):
    """Test correct LLM instantiation based on model name."""

    # Test OpenAI
    llm = chat_chain._select_llm("gpt-4")
    mock_dependencies["GPTClass"].assert_called_with(model_name="gpt-4")

    # Test Claude
    llm = chat_chain._select_llm("claude-3-opus")
    mock_dependencies["ClaudeClass"].assert_called_with(model_name="claude-3-opus")

    # Test Novita (gemma/gpt-oss)
    llm = chat_chain._select_llm("gemma-2b")
    mock_dependencies["NovitaClass"].assert_called_with(model_name="gemma-2b")

    # Test Fallback
    llm = chat_chain._select_llm("unknown-model")
    assert llm == chat_chain.fallback_llm


@pytest.mark.asyncio
async def test_generate_answer_flow(chat_chain, mock_dependencies):
    """Test full answer generation flow."""
    user_id = "test_user"
    query = "test query"

    answer = await chat_chain.generate_answer(
        user_id=user_id, query=query, model_name="gpt-4.1", stream=False
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
        user_id="u1", query="q", model_name="gpt-4", stream=True
    )

    parts = []
    async for chunk in response_gen:
        if isinstance(chunk, str):
            parts.append(chunk)

    assert "".join(parts) == "Part 1Part 2"
