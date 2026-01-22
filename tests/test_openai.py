from unittest.mock import AsyncMock, Mock, patch

import os
import openai
import pytest

from app.llms.gpt import ChatGPT


@pytest.mark.unit
@pytest.mark.openai
class TestChatGPT:
    """Test ChatGPT LLM service."""

    @pytest.fixture
    def chatgpt_handler(self, test_env_vars):
        """Create ChatGPT instance for testing."""
        with patch("app.llms.gpt.AsyncOpenAI") as mock_client:
            mock_instance = Mock()
            mock_client.return_value = mock_instance

            handler = ChatGPT(model_name="gpt-4")
            handler.client = mock_instance
            return handler

    @pytest.fixture
    def mock_completion_response(self):
        """Mock OpenAI completion response."""
        mock_response = Mock()
        mock_choice = Mock()
        mock_choice.message.content = "This is a test response from the AI assistant."
        mock_response.choices = [mock_choice]
        return mock_response

    @pytest.fixture
    def mock_streaming_response(self):
        """Mock OpenAI streaming response."""
        chunks = []
        test_content = "This is a streaming response"

        for char in test_content:
            mock_chunk = Mock()
            mock_delta = Mock()
            mock_delta.content = char
            mock_choice = Mock()
            mock_choice.delta = mock_delta
            mock_chunk.choices = [mock_choice]
            chunks.append(mock_chunk)

        return chunks

    def test_initialization(self, test_env_vars):
        """Test ChatGPT initialization with default and custom models."""
        with patch("app.llms.gpt.AsyncOpenAI") as mock_client:
            mock_instance = Mock()
            mock_client.return_value = mock_instance

            # Test default initialization
            handler1 = ChatGPT()
            mock_client.assert_called_with(
                api_key=os.getenv("OPENAI_API_KEY")
            )  # pragma: allowlist secret
            assert handler1.model == "gpt-4o-mini"  # Default from settings

            # Test custom model initialization
            handler2 = ChatGPT(model_name="gpt-4-turbo")
            assert handler2.model == "gpt-4-turbo"

    @patch("app.llms.gpt.settings.STREAM", False)
    async def test_generate_complete_response(
        self, chatgpt_handler, mock_completion_response
    ):
        """Test generating complete (non-streaming) response."""
        chatgpt_handler.client.chat.completions.create = AsyncMock(
            return_value=mock_completion_response
        )

        result = await chatgpt_handler.generate_response(
            user_prompt="What is AI?", system_prompt="You are a helpful assistant."
        )

        assert isinstance(result, str)
        assert result == "This is a test response from the AI assistant."

        # Verify API call parameters
        chatgpt_handler.client.chat.completions.create.assert_called_once()
        call_kwargs = chatgpt_handler.client.chat.completions.create.call_args[1]

        assert call_kwargs["model"] == "gpt-4"
        assert call_kwargs["messages"][0]["role"] == "system"
        assert call_kwargs["messages"][0]["content"] == "You are a helpful assistant."
        assert call_kwargs["messages"][1]["role"] == "user"
        assert call_kwargs["messages"][1]["content"] == "What is AI?"

    @patch("app.llms.gpt.settings.STREAM", True)
    async def test_generate_streaming_response(
        self, chatgpt_handler, mock_streaming_response
    ):
        """Test generating streaming response."""
        mock_async_iter = AsyncMock()
        mock_async_iter.__aiter__.return_value = mock_streaming_response
        chatgpt_handler.client.chat.completions.create = AsyncMock(
            return_value=mock_async_iter
        )

        result = chatgpt_handler.generate_response(
            user_prompt="Tell me a story", system_prompt="You are a storyteller."
        )

        # Should return async generator
        assert hasattr(result, "__aiter__")

        # Collect all chunks
        collected_chunks = []
        async for chunk in result:
            collected_chunks.append(chunk)

        expected_content = "This is a streaming response"
        assert "".join(collected_chunks) == expected_content

    async def test_generate_response_with_exception(self, chatgpt_handler):
        """Test handling of exceptions during response generation."""
        # chatgpt_handler.client.chat.completions.create = AsyncMock(
        #     side_effect=openai.APIError("API Error")
        # )

        with pytest.raises(openai.APIError):
            await chatgpt_handler.generate_response(
                user_prompt="Test", system_prompt="Test"
            )

    async def test_generate_complete_with_different_models(
        self, chatgpt_handler, mock_completion_response
    ):
        """Test token parameter selection for different models."""
        chatgpt_handler.model = "gpt-5.2"
        chatgpt_handler.client.chat.completions.create = AsyncMock(
            return_value=mock_completion_response
        )

        await chatgpt_handler._generate_complete(system_prompt="Test", query="Test")

        call_kwargs = chatgpt_handler.client.chat.completions.create.call_args[1]
        assert "max_completion_tokens" in call_kwargs
        assert "max_tokens" not in call_kwargs

    async def test_generate_complete_with_standard_model(
        self, chatgpt_handler, mock_completion_response
    ):
        """Test token parameter for standard GPT models."""
        chatgpt_handler.model = "gpt-4"
        chatgpt_handler.client.chat.completions.create = AsyncMock(
            return_value=mock_completion_response
        )

        await chatgpt_handler._generate_complete(system_prompt="Test", query="Test")

        call_kwargs = chatgpt_handler.client.chat.completions.create.call_args[1]
        assert "max_tokens" in call_kwargs
        assert "max_completion_tokens" not in call_kwargs

    async def test_generate_streaming_with_o1_model(
        self, chatgpt_handler, mock_streaming_response
    ):
        """Test streaming with O1 model token parameter."""
        chatgpt_handler.model = "o1-preview"
        mock_async_iter = AsyncMock()
        mock_async_iter.__aiter__.return_value = mock_streaming_response
        chatgpt_handler.client.chat.completions.create = AsyncMock(
            return_value=mock_async_iter
        )

        result = chatgpt_handler.generate_response(
            user_prompt="Test", system_prompt="Test"
        )

        # Consume the generator
        async for _ in result:
            pass

        call_kwargs = chatgpt_handler.client.chat.completions.create.call_args[1]
        assert "max_completion_tokens" in call_kwargs
        assert "max_tokens" not in call_kwargs

    async def test_response_with_empty_content(self, chatgpt_handler):
        """Test handling of empty response content."""
        mock_response = Mock()
        mock_choice = Mock()
        mock_choice.message.content = ""
        mock_response.choices = [mock_choice]

        chatgpt_handler.client.chat.completions.create = AsyncMock(
            return_value=mock_response
        )

        result = await chatgpt_handler.generate_response(
            user_prompt="Test", system_prompt="Test"
        )

        assert result == ""

    async def test_response_with_none_content(self, chatgpt_handler):
        """Test handling of None response content."""
        mock_response = Mock()
        mock_choice = Mock()
        mock_choice.message.content = None
        mock_response.choices = [mock_choice]

        chatgpt_handler.client.chat.completions.create = AsyncMock(
            return_value=mock_response
        )

        result = await chatgpt_handler.generate_response(
            user_prompt="Test", system_prompt="Test"
        )

        assert result is None

    async def test_generate_with_temperature(
        self, chatgpt_handler, mock_completion_response
    ):
        """Test that temperature parameter is passed correctly."""
        chatgpt_handler.client.chat.completions.create = AsyncMock(
            return_value=mock_completion_response
        )

        with patch("app.llms.gpt.settings.TEMPERATURE", 0.7):
            await chatgpt_handler._generate_complete(system_prompt="Test", query="Test")

        call_kwargs = chatgpt_handler.client.chat.completions.create.call_args[1]
        assert call_kwargs["temperature"] == 0.7

    async def test_generate_with_max_tokens(
        self, chatgpt_handler, mock_completion_response
    ):
        """Test that max tokens parameter is passed correctly."""
        chatgpt_handler.client.chat.completions.create = AsyncMock(
            return_value=mock_completion_response
        )

        with patch("app.llms.gpt.settings.OUTPUT_MAX_TOKENS", 1000):
            await chatgpt_handler._generate_complete(system_prompt="Test", query="Test")

        call_kwargs = chatgpt_handler.client.chat.completions.create.call_args[1]
        assert call_kwargs["max_tokens"] == 1000

    async def test_streaming_with_none_content_chunks(self, chatgpt_handler):
        """Test streaming when some chunks have None content."""
        mock_chunk_with_content = Mock()
        mock_delta = Mock()
        mock_delta.content = "test"
        mock_choice = Mock()
        mock_choice.delta = mock_delta
        mock_chunk_with_content.choices = [mock_choice]

        mock_chunk_no_content = Mock()
        mock_delta_none = Mock()
        mock_delta_none.content = None
        mock_choice_none = Mock()
        mock_choice_none.delta = mock_delta_none
        mock_chunk_no_content.choices = [mock_choice_none]

        chunks = [mock_chunk_no_content, mock_chunk_with_content, mock_chunk_no_content]

        mock_async_iter = AsyncMock()
        mock_async_iter.__aiter__.return_value = chunks
        chatgpt_handler.client.chat.completions.create = AsyncMock(
            return_value=mock_async_iter
        )

        result = chatgpt_handler.generate_response(
            user_prompt="Test", system_prompt="Test"
        )

        collected_chunks = []
        async for chunk in result:
            if chunk:  # Only collect non-None chunks
                collected_chunks.append(chunk)

        assert collected_chunks == ["test"]

    def test_chatgpt_inheritance_from_llm(self):
        """Test that ChatGPT properly inherits from LLM base class."""
        from app.llms.base import LLM

        assert issubclass(ChatGPT, LLM)

        # Test that required methods are implemented
        handler = ChatGPT()
        assert hasattr(handler, "generate_response")
        assert callable(getattr(handler, "generate_response"))


@pytest.mark.integration
@pytest.mark.openai
@pytest.mark.slow
class TestOpenAIIntegration:
    """Integration tests for OpenAI (requires actual API access)."""

    @pytest.fixture(autouse=True)
    def setup_integration(self):
        """Setup for integration tests."""
        pytest.skip("Integration test - set RUN_INTEGRATION_TESTS=1 to run")

    async def test_real_openai_call(self):
        """Test real OpenAI API call (integration test)."""
        # This test would require real OpenAI API credentials
        # and should be run only in CI/CD environment
        pass
