from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from openai import AuthenticationError

from app.llms.gpt import ChatGPT


@pytest.fixture
def mock_settings():
    with patch("app.llms.gpt.settings") as mock_settings:
        mock_settings.OPENAI_API_KEY = "test-key"
        mock_settings.GPT_COMPLETION_MODEL = "gpt-4.1"
        mock_settings.TEMPERATURE = 0.5
        mock_settings.OUTPUT_MAX_TOKENS = 100
        mock_settings.STREAM = True
        yield mock_settings


@pytest.fixture
def mock_openai_client():
    with patch("app.llms.gpt.AsyncOpenAI") as mock_cls:
        yield mock_cls


@pytest.mark.asyncio
async def test_init(mock_settings, mock_openai_client):
    """Test initialization with API key."""
    llm = ChatGPT()
    mock_openai_client.assert_called_with(api_key="test-key")
    assert llm.model == "gpt-4.1"


@pytest.mark.asyncio
async def test_auth_failure(mock_settings, mock_openai_client):
    """Test proper error propagation on auth failure."""
    # Setup mock instance
    mock_instance = mock_openai_client.return_value
    # Setup create method to raise Auth Error
    mock_instance.chat.completions.create.side_effect = AuthenticationError(
        message="Invalid API Key", response=MagicMock(), body={}
    )

    llm = ChatGPT()

    with pytest.raises(AuthenticationError):
        await llm.generate_response("user", "system", stream=False)


@pytest.mark.asyncio
async def test_streaming_fully(mock_settings, mock_openai_client):
    """
    Test full streaming flow:
    1. Verify correct params passed to client
    2. Verify generator yields chunks correctly
    3. Verify empty chunks are skipped
    """
    llm = ChatGPT()
    mock_instance = mock_openai_client.return_value

    # Mock specific Chunks
    chunk1 = MagicMock()
    chunk1.choices[0].delta.content = "Hello "

    chunk2 = MagicMock()
    chunk2.choices[0].delta.content = "World"

    chunk_empty = MagicMock()
    chunk_empty.choices[0].delta.content = None  # Should be skipped

    # Helper to create async iterator for the "response" object
    async def async_iter(items):
        for item in items:
            yield item

    # The create method is awaited, so it must return the async iterator (the stream)
    # We use AsyncMock for create so it can be awaited
    stream_response = async_iter([chunk1, chunk_empty, chunk2])
    mock_instance.chat.completions.create = AsyncMock(return_value=stream_response)

    # Act
    gen = await llm.generate_response("user query", "system prompt", stream=True)

    # Collect results
    result = []
    async for part in gen:
        result.append(part)

    # Assert
    assert result == ["Hello ", "World"]

    # Verify call arguments
    mock_instance.chat.completions.create.assert_called_once()
    call_kwargs = mock_instance.chat.completions.create.call_args[1]

    assert call_kwargs["stream"] is True
    assert call_kwargs["messages"] == [
        {"role": "system", "content": "system prompt"},
        {"role": "user", "content": "user query"},
    ]
    # Check regular model parameter
    assert "max_tokens" in call_kwargs


@pytest.mark.asyncio
async def test_special_model_params(mock_settings, mock_openai_client):
    """Test that 'o1' or 'gpt-5.2' models use max_completion_tokens."""
    llm = ChatGPT(model_name="o1-preview")
    mock_instance = mock_openai_client.return_value

    # Mock the response object
    mock_response = MagicMock()
    mock_response.choices[0].message.content = "special model response"

    # Make create awaitable and return the mock_response
    mock_instance.chat.completions.create = AsyncMock(return_value=mock_response)

    await llm.generate_response("u", "s", stream=False)

    call_kwargs = mock_instance.chat.completions.create.call_args[1]
    assert "max_completion_tokens" in call_kwargs
    assert "max_tokens" not in call_kwargs
