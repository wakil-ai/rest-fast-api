from unittest.mock import AsyncMock, patch

import pytest

from app.chains.chat_chain import ChatChain


@pytest.fixture
def mock_dependencies():
    with (
        patch("app.chains.chat_chain.RetrievalService") as mock_ret,
        patch("app.chains.chat_chain.ChatMemoryService") as mock_mem,
        patch("app.chains.chat_chain.ChatGPT") as mock_gpt,
        patch("app.chains.chat_chain.Claude") as mock_claude,
        patch("app.chains.chat_chain.Novita") as mock_novita,
    ):
        # Setup Instances
        mock_ret_instance = mock_ret.return_value
        mock_mem_instance = mock_mem.return_value

        # Setup default async returns
        mock_mem_instance.search_memory = AsyncMock(return_value="")
        mock_ret_instance.retrieve_context = AsyncMock(return_value="Valid Context")

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
    # This calls _get_llm_by_model internally or we call it directly

    # Test OpenAI
    llm = chat_chain._get_llm("gpt-4")
    # factory returns a new instance, so we check if the class was initialized with model name
    mock_dependencies["GPTClass"].assert_called_with(model_name="gpt-4")

    # Test Claude
    llm = chat_chain._get_llm("claude-3-opus")
    mock_dependencies["ClaudeClass"].assert_called_with(model_name="claude-3-opus")

    # Test Novita (gemma/gpt-oss)
    llm = chat_chain._get_llm("gemma-2b")
    mock_dependencies["NovitaClass"].assert_called_with(model_name="gemma-2b")

    # Test Fallback
    llm = chat_chain._get_llm("unknown-model")
    # Should return fallback (ChatGPT instance created in __init__)
    # We verify it's the fallback instance stored on self.llm_fallback
    assert llm == chat_chain.fallback_llm


@pytest.mark.asyncio
async def test_generate_answer_flow(chat_chain, mock_dependencies):
    """Test full answer generation flow."""
    # Setup
    user_id = "test_user"
    query = "test query"

    # Act
    answer = await chat_chain.generate_answer(
        user_id=user_id, query=query, model_name="gpt-4.1", stream=False
    )

    assert answer

    # Check context retrieval
    mock_dependencies["retrieval"].retrieve_context.assert_called_once()

    # Check memory search
    mock_dependencies["memory"].search_memory.assert_awaited_with(user_id, query)

    # Check LLM generation
    mock_dependencies["gpt"].generate_response.assert_awaited()
    # verify system prompt contains context
    call_kwargs = mock_dependencies["gpt"].generate_response.call_args[1]
    assert "Valid Context" in call_kwargs["system_prompt"]


@pytest.mark.asyncio
async def test_streaming_response(chat_chain, mock_dependencies):
    """Test streaming response generator."""

    # Setup LLM to return a generator
    async def async_gen():
        yield "Part 1"
        yield "Part 2"

    mock_dependencies["gpt"].generate_response.return_value = async_gen()

    # Act
    response_gen = await chat_chain.generate_answer(
        user_id="u1", query="q", model_name="gpt-4", stream=True
    )

    # Collect parts
    parts = []
    async for chunk in response_gen:
        # Filter out debug dictionaries if any
        if isinstance(chunk, str):
            parts.append(chunk)

    assert "".join(parts) == "Part 1Part 2"


@pytest.mark.asyncio
async def test_project_context_retrieval(chat_chain, mock_dependencies):
    """Test dual retrieval when project_id is provided."""
    # Setup retrieval service to return specific things
    mock_dependencies["retrieval"].retrieve_project_context = AsyncMock(
        return_value="Project Docs"
    )
    mock_dependencies["retrieval"].retrieve_context = AsyncMock(
        return_value="General Laws"
    )

    mock_dependencies["gpt"].generate_response.return_value = "Answer"

    await chat_chain.generate_answer(
        user_id="u1", query="q", project_id="proj_123", model_name="gpt-4", stream=False
    )

    # Verify both retrieval methods called
    mock_dependencies["retrieval"].retrieve_project_context.assert_called_once()
    mock_dependencies["retrieval"].retrieve_context.assert_called_once()

    # Verify prompt contains combined context
    sys_prompt = mock_dependencies["gpt"].generate_response.call_args[1][
        "system_prompt"
    ]
    assert "Project Docs" in sys_prompt
    assert "General Laws" in sys_prompt
