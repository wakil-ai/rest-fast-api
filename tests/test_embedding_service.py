from unittest.mock import Mock, patch

import pytest

from app.core.config import settings
from app.retrieval.embedding_manager import EmbeddingManager, SiliconFlowEmbedding


class TestSiliconFlowEmbedding:
    """Test SiliconFlow embedding service connection and functionality."""

    @pytest.fixture
    def embedding_service(self):
        """Create SiliconFlow embedding instance for testing."""
        with patch.object(settings, "SILICONFLOW_API_KEY", "test-key"):
            with patch.object(
                settings,
                "SILICONFLOW_EMBEDDING_BASE_URL",
                "https://api.siliconflow.com/v1/embeddings",
            ):
                with patch.object(
                    settings, "SILICONFLOW_EMBEDDING_MODEL", "Qwen/Qwen3-Embedding-4B"
                ):
                    return SiliconFlowEmbedding()

    @pytest.fixture
    def mock_response_data(self):
        """Mock embedding response data."""
        return {
            "data": [{"embedding": [0.1] * 2560}]  # Mock 2560-dimensional embedding
        }

    @patch("app.retrieval.embedding_manager.requests.post")
    def test_connection_check(self, mock_post, embedding_service):
        """Test connection to SiliconFlow API."""
        mock_post.return_value.raise_for_status.return_value = None

        embedding_service.embed_doc("test text")

        mock_post.assert_called_once()
        call_args = mock_post.call_args
        assert call_args[1]["json"]["input"] == "test text"
        assert call_args[1]["headers"]["Authorization"] == "Bearer test-key"

    @patch("app.retrieval.embedding_manager.requests.post")
    def test_embedding_generation_single_document(
        self, mock_post, embedding_service, mock_response_data
    ):
        """Test embedding generation for a single document."""
        mock_response = Mock()
        mock_response.json.return_value = mock_response_data
        mock_response.raise_for_status.return_value = None
        mock_post.return_value = mock_response

        test_text = "This is a test document for embedding generation."
        embedding = embedding_service.embed_doc(test_text)

        assert isinstance(embedding, list)
        assert len(embedding) == 2560  # Qwen3-Embedding-4B dimension
        assert all(isinstance(x, float) for x in embedding)

        mock_post.assert_called_once_with(
            "https://api.siliconflow.com/v1/embeddings",
            headers=embedding_service.headers,
            json={
                "model": "Qwen/Qwen3-Embedding-4B",
                "input": test_text,
                "encoding_format": "float",
            },
        )

    @patch("app.retrieval.embedding_manager.requests.post")
    def test_embedding_generation_batch(self, mock_post, embedding_service):
        """Test batch embedding generation."""
        mock_response_data = {
            "data": [
                {"embedding": [0.1] * 2560},
                {"embedding": [0.2] * 2560},
                {"embedding": [0.3] * 2560},
            ]
        }
        mock_response = Mock()
        mock_response.json.return_value = mock_response_data
        mock_response.raise_for_status.return_value = None
        mock_post.return_value = mock_response

        test_texts = [
            "First test document",
            "Second test document",
            "Third test document",
        ]
        embeddings = embedding_service.embed_batch(test_texts)

        assert isinstance(embeddings, list)
        assert len(embeddings) == 3
        assert all(isinstance(emb, list) for emb in embeddings)
        assert all(len(emb) == 2560 for emb in embeddings)

        mock_post.assert_called_once_with(
            "https://api.siliconflow.com/v1/embeddings",
            headers=embedding_service.headers,
            json={
                "model": "Qwen/Qwen3-Embedding-4B",
                "input": test_texts,
                "encoding_format": "float",
            },
        )

    @patch("app.retrieval.embedding_manager.requests.post")
    def test_embedding_generation_query_with_instruction(
        self, mock_post, embedding_service, mock_response_data
    ):
        """Test embedding generation for query with instruction."""
        mock_response = Mock()
        mock_response.json.return_value = mock_response_data
        mock_response.raise_for_status.return_value = None
        mock_post.return_value = mock_response

        query = "What are the legal requirements for contract formation?"
        embedding = embedding_service.embed_query(query)

        assert isinstance(embedding, list)
        assert len(embedding) == 2560

        # Verify that instruction was added to the query
        expected_instruction = (
            "Instruct: Given a legal question from Uzbek Law, retrieve the most relevant "
            "legal documents and semantically similar documents to answer the question. \n"
            f"Query: {query}"
        )
        mock_post.assert_called_once_with(
            "https://api.siliconflow.com/v1/embeddings",
            headers=embedding_service.headers,
            json={
                "model": "Qwen/Qwen3-Embedding-4B",
                "input": expected_instruction,
                "encoding_format": "float",
            },
        )

    @patch("app.retrieval.embedding_manager.requests.post")
    def test_connection_error_handling(self, mock_post, embedding_service):
        """Test error handling for connection failures."""
        import requests

        mock_post.side_effect = requests.RequestException("Connection failed")

        with pytest.raises(requests.RequestException):
            embedding_service.embed_doc("test text")

    @patch("app.retrieval.embedding_manager.requests.post")
    def test_empty_batch_handling(self, mock_post, embedding_service):
        """Test handling of empty batch."""
        embeddings = embedding_service.embed_batch([])

        assert embeddings == []
        mock_post.assert_not_called()

    @patch("app.retrieval.embedding_manager.requests.post")
    def test_api_response_error_handling(self, mock_post, embedding_service):
        """Test handling of API error responses."""
        import requests

        mock_response = Mock()
        mock_response.raise_for_status.side_effect = requests.HTTPError(
            "API Error: 400"
        )
        mock_post.return_value = mock_response

        with pytest.raises(requests.HTTPError):
            embedding_service.embed_doc("test text")


class TestEmbeddingManager:
    """Test EmbeddingManager singleton and integration."""

    @patch("app.retrieval.embedding_manager.settings.EMBEDDING_MODEL", "siliconflow")
    @patch.object(SiliconFlowEmbedding, "__init__", return_value=None)
    def test_embedding_manager_initialization(self, mock_siliconflow_init):
        """Test EmbeddingManager singleton initialization."""
        # Reset singleton for testing
        EmbeddingManager._instance = None
        EmbeddingManager._initialized = False

        manager = EmbeddingManager()

        assert manager is not None
        mock_siliconflow_init.assert_called_once()

    @patch("app.retrieval.embedding_manager.settings.EMBEDDING_MODEL", "siliconflow")
    def test_embedding_manager_singleton(self):
        """Test EmbeddingManager singleton behavior."""
        # Reset singleton for testing
        EmbeddingManager._instance = None
        EmbeddingManager._initialized = False

        manager1 = EmbeddingManager()
        manager2 = EmbeddingManager()

        assert manager1 is manager2

    @patch("app.retrieval.embedding_manager.settings.EMBEDDING_MODEL", "siliconflow")
    def test_embedding_manager_delegate_methods(self):
        """Test that EmbeddingManager properly delegates to embedding implementation."""
        # Reset singleton for testing
        EmbeddingManager._instance = None
        EmbeddingManager._initialized = False

        with patch.object(
            SiliconFlowEmbedding, "embed_doc", return_value=[0.1] * 2560
        ) as mock_embed:
            manager = EmbeddingManager()
            result = manager.embed_doc("test")

            mock_embed.assert_called_once_with("test")
            assert result == [0.1] * 2560


if __name__ == "__main__":
    pytest.main([__file__])
