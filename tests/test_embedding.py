from unittest.mock import Mock, patch

import pytest
import requests

from app.retrieval.embedding_manager import (
    EmbeddingManager,
    SiliconFlowEmbedding,
    get_instruction,
)


@pytest.mark.unit
@pytest.mark.embedding
class TestSiliconFlowEmbedding:
    """Test SiliconFlow embedding service."""

    @pytest.fixture
    def embedding_service(self, test_env_vars):
        """Create SiliconFlowEmbedding instance for testing."""
        with patch("app.retrieval.embedding_manager.settings"):
            service = SiliconFlowEmbedding()
            service.api_url = "https://api.siliconflow.cn/v1/embeddings"
            service.api_key = "test-api-key"  # pragma: allowlist secret
            service.model_name = "BAAI/bge-m3"
            return service

    def test_get_instruction(self):
        """Test instruction generation for queries."""
        query = "What is the law about contracts?"
        instruction = get_instruction(query)

        assert "Instruct:" in instruction
        assert query in instruction
        assert "legal question" in instruction

    @patch("app.retrieval.embedding_manager.requests.post")
    def test_embed_doc_success(
        self, mock_post, embedding_service, mock_embedding_response
    ):
        """Test successful document embedding."""
        # Mock response
        mock_response = Mock()
        mock_response.raise_for_status.return_value = None
        mock_response.json.return_value = {
            "data": [{"embedding": mock_embedding_response}]
        }
        mock_post.return_value = mock_response

        result = embedding_service.embed_doc("Test document content")

        assert isinstance(result, list)
        assert len(result) == len(mock_embedding_response)
        assert all(isinstance(x, float) for x in result)

        # Verify request was made correctly
        mock_post.assert_called_once()
        call_args = mock_post.call_args
        assert call_args[1]["json"]["model"] == embedding_service.model_name
        assert call_args[1]["json"]["input"] == "Test document content"

    @patch("app.retrieval.embedding_manager.requests.post")
    def test_embed_query_success(
        self, mock_post, embedding_service, mock_embedding_response
    ):
        """Test successful query embedding with instruction."""
        # Mock response
        mock_response = Mock()
        mock_response.raise_for_status.return_value = None
        mock_response.json.return_value = {
            "data": [{"embedding": mock_embedding_response}]
        }
        mock_post.return_value = mock_response

        query = "Test legal question"
        result = embedding_service.embed_query(query)

        assert isinstance(result, list)
        assert len(result) == len(mock_embedding_response)

        # Verify that instruction was added to query
        call_args = mock_post.call_args
        sent_input = call_args[1]["json"]["input"]
        assert "Instruct:" in sent_input
        assert query in sent_input

    @patch("app.retrieval.embedding_manager.requests.post")
    def test_embed_batch_success(self, mock_post, embedding_service):
        """Test successful batch embedding."""
        # Mock response
        mock_response = Mock()
        mock_response.raise_for_status.return_value = None
        mock_response.json.return_value = {
            "data": [
                {"embedding": [0.1] * 1536},
                {"embedding": [0.2] * 1536},
                {"embedding": [0.3] * 1536},
            ]
        }
        mock_post.return_value = mock_response

        texts = ["Document 1", "Document 2", "Document 3"]
        result = embedding_service.embed_batch(texts)

        assert isinstance(result, list)
        assert len(result) == 3
        assert all(isinstance(embedding, list) for embedding in result)
        assert all(len(embedding) == 1536 for embedding in result)

    @patch("app.retrieval.embedding_manager.requests.post")
    def test_embed_batch_empty_list(self, mock_post, embedding_service):
        """Test batch embedding with empty list."""
        result = embedding_service.embed_batch([])

        assert result == []
        mock_post.assert_not_called()

    @patch("app.retrieval.embedding_manager.requests.post")
    def test_connection_error(self, mock_post, embedding_service):
        """Test handling of connection errors."""
        mock_post.side_effect = requests.exceptions.ConnectionError("Connection failed")

        with pytest.raises(requests.exceptions.ConnectionError):
            embedding_service.embed_doc("Test content")

    @patch("app.retrieval.embedding_manager.requests.post")
    def test_http_error(self, mock_post, embedding_service):
        """Test handling of HTTP errors."""
        mock_response = Mock()
        mock_response.raise_for_status.side_effect = requests.exceptions.HTTPError(
            "404 Not Found"
        )
        mock_post.return_value = mock_response

        with pytest.raises(requests.exceptions.HTTPError):
            embedding_service.embed_doc("Test content")

    @patch("app.retrieval.embedding_manager.requests.post")
    def test_invalid_response_format(self, mock_post, embedding_service):
        """Test handling of invalid response format."""
        mock_response = Mock()
        mock_response.raise_for_status.return_value = None
        mock_response.json.return_value = {"invalid": "format"}  # Missing "data" key
        mock_post.return_value = mock_response

        with pytest.raises(KeyError):
            embedding_service.embed_doc("Test content")


@pytest.mark.unit
@pytest.mark.embedding
class TestEmbeddingManager:
    """Test EmbeddingManager singleton pattern and integration."""

    @patch("app.retrieval.embedding_manager.settings")
    def test_singleton_pattern(self, mock_settings):
        """Test that EmbeddingManager follows singleton pattern."""
        mock_settings.EMBEDDING_MODEL = "siliconflow"

        with patch(
            "app.retrieval.embedding_manager.SiliconFlowEmbedding"
        ) as mock_class:
            mock_instance = Mock()
            mock_instance.embed_query.return_value = [0.1] * 1536
            mock_class.return_value = mock_instance

            manager1 = EmbeddingManager()
            manager2 = EmbeddingManager()

            assert manager1 is manager2
            mock_class.assert_called_once()

    @patch("app.retrieval.embedding_manager.settings")
    def test_embed_query_delegation(self, mock_settings):
        """Test that EmbeddingManager delegates to embedding service."""
        mock_settings.EMBEDDING_MODEL = "siliconflow"

        with patch(
            "app.retrieval.embedding_manager.SiliconFlowEmbedding"
        ) as mock_class:
            mock_instance = Mock()
            mock_instance.embed_query.return_value = [0.1] * 1536
            mock_class.return_value = mock_instance

            manager = EmbeddingManager()
            query = "Test query"
            result = manager.embed_query(query)

            mock_instance.embed_query.assert_called_once_with(query)
            assert result == [0.1] * 1536

    @patch("app.retrieval.embedding_manager.settings")
    def test_embed_doc_delegation(self, mock_settings):
        """Test that EmbeddingManager delegates document embedding."""
        mock_settings.EMBEDDING_MODEL = "siliconflow"

        with patch(
            "app.retrieval.embedding_manager.SiliconFlowEmbedding"
        ) as mock_class:
            mock_instance = Mock()
            mock_instance.embed_doc.return_value = [0.2] * 1536
            mock_class.return_value = mock_instance

            manager = EmbeddingManager()
            doc = "Test document"
            result = manager.embed_doc(doc)

            mock_instance.embed_doc.assert_called_once_with(doc)
            assert result == [0.2] * 1536

    @patch("app.retrieval.embedding_manager.settings")
    def test_embed_batch_delegation(self, mock_settings):
        """Test that EmbeddingManager delegates batch embedding."""
        mock_settings.EMBEDDING_MODEL = "siliconflow"

        with patch(
            "app.retrieval.embedding_manager.SiliconFlowEmbedding"
        ) as mock_class:
            mock_instance = Mock()
            mock_instance.embed_batch.return_value = [[0.1] * 1536, [0.2] * 1536]
            mock_class.return_value = mock_instance

            manager = EmbeddingManager()
            texts = ["Doc 1", "Doc 2"]
            result = manager.embed_batch(texts)

            mock_instance.embed_batch.assert_called_once_with(texts)
            assert result == [[0.1] * 1536, [0.2] * 1536]


@pytest.mark.integration
@pytest.mark.embedding
@pytest.mark.slow
class TestEmbeddingIntegration:
    """Integration tests for embedding service (requires actual API access)."""

    @pytest.fixture(autouse=True)
    def setup_integration(self):
        """Setup for integration tests."""
        pytest.skip("Integration test - set RUN_INTEGRATION_TESTS=1 to run")

    def test_real_embedding_computation(self):
        """Test real embedding computation (integration test)."""
        # This test would require real API credentials
        # and should be run only in CI/CD environment
        pass
