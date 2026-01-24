from unittest.mock import Mock, patch

import pytest

from app.retrieval.embedding_manager import (
    SiliconFlowEmbedding,
    get_instruction,
)


# Unit tests for functions
def test_get_instruction():
    """Test get_instruction format."""
    query = "test query"
    # Matches the exact string formatted in the code including indentation spaces
    expected = (
        "Instruct: Given a legal question from Uzbek Law, retrieve the most relevant legal documents "
        "        and semantically similar documents to answer the question. \nQuery: test query"
    )
    assert get_instruction(query) == expected


class TestSiliconFlowEmbedding:
    @pytest.fixture
    def mock_settings(self):
        with patch("app.retrieval.embedding_manager.settings") as mock_settings:
            mock_settings.SILICONFLOW_EMBEDDING_BASE_URL = (
                "https://api.siliconflow.cn/v1/embeddings"
            )
            mock_settings.SILICONFLOW_API_KEY = "test-sf-key"
            mock_settings.SILICONFLOW_EMBEDDING_MODEL = "Qwen/Qwen3-Embedding-4B"
            yield mock_settings

    @pytest.fixture
    def embedding_service(self, mock_settings):
        return SiliconFlowEmbedding()

    def test_init(self, mock_settings):
        service = SiliconFlowEmbedding()
        assert service.api_key == "test-sf-key"
        assert service.headers["Authorization"] == "Bearer test-sf-key"
        assert service.headers["Content-Type"] == "application/json"

    def test_embed_doc_success(self, embedding_service):
        """Test embed_doc returns correct shape (2560) using a mock."""
        mock_response = Mock()
        # Mocking a 2560-dimensional embedding
        expected_embedding = [0.1] * 2560
        mock_response.json.return_value = {"data": [{"embedding": expected_embedding}]}
        mock_response.raise_for_status.return_value = None

        with patch("requests.post", return_value=mock_response) as mock_post:
            embedding = embedding_service.embed_doc("test document")

            assert len(embedding) == 2560

            # Verify request call
            mock_post.assert_called_once()
            args, kwargs = mock_post.call_args
            assert kwargs["json"]["input"] == "test document"
            assert kwargs["headers"]["Authorization"] == "Bearer test-sf-key"

    def test_embed_query_success(self, embedding_service):
        """Test embed_query adds instruction and returns correct shape."""
        mock_response = Mock()
        expected_embedding = [0.1] * 2560
        mock_response.json.return_value = {"data": [{"embedding": expected_embedding}]}
        mock_response.raise_for_status.return_value = None

        with patch("requests.post", return_value=mock_response) as mock_post:
            query = "test query"
            embedding = embedding_service.embed_query(query)

            assert len(embedding) == 2560

            # Verify instruction was added in the input
            call_kwargs = mock_post.call_args[1]
            input_text = call_kwargs["json"]["input"]
            assert "Instruct: Given a legal question" in input_text
            assert query in input_text

    def test_auth_failure(self, embedding_service):
        """Test handling of authentication failure (401)."""
        import requests

        mock_response = Mock()
        # Raise a RequestException so it's caught by the except block
        mock_response.raise_for_status.side_effect = requests.exceptions.HTTPError(
            "401 Client Error"
        )

        with patch("requests.post", return_value=mock_response):
            with pytest.raises(requests.exceptions.HTTPError) as excinfo:
                # The method re-raises the exception, so we expect HTTPError
                embedding_service.embed_doc("test")
            # The exception message is just the one from the HTTPError
            assert "401 Client Error" in str(excinfo.value)
