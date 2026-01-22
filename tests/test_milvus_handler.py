from unittest.mock import Mock, patch

import pytest
from pymilvus import MilvusClient

from app.core.config import settings
from app.db.milvus_handler import MilvusHandler


class TestMilvusHandler:
    """Test Milvus vector database connection and retrieval functionality."""

    @pytest.fixture
    def mock_milvus_client(self):
        """Create a mock Milvus client."""
        mock_client = Mock(spec=MilvusClient)
        mock_client.has_collection.return_value = False
        mock_client.has_partition.return_value = False
        mock_client.create_collection.return_value = None
        mock_client.create_index.return_value = None
        mock_client.load_collection.return_value = None
        mock_client.create_partition.return_value = None
        mock_client.insert.return_value = None
        mock_client.search.return_value = [[]]  # Default empty search result
        mock_client.hybrid_search.return_value = [[]]
        return mock_client

    @pytest.fixture
    def milvus_handler(self, mock_milvus_client):
        """Create MilvusHandler instance with mocked client."""
        with patch(
            "app.db.milvus_handler.MilvusClient", return_value=mock_milvus_client
        ):
            with patch.object(settings, "MILVUS_URI", "http://localhost:19530"):
                with patch.object(settings, "MILVUS_USER", "test_user"):
                    with patch.object(settings, "MILVUS_PASSWORD", "test_pass"):
                        with patch.object(settings, "EMBEDDING_DIM", 2560):
                            return MilvusHandler()

    @pytest.fixture
    def sample_documents(self):
        """Sample documents for testing."""
        return [
            {
                "id": "doc1",
                "embedding": [0.1] * 2560,
                "metadata": {
                    "text": "Sample legal document about contracts",
                    "hierarchy_path": "civil_law/contracts",
                    "source": "lex.uz",
                },
            },
            {
                "id": "doc2",
                "embedding": [0.2] * 2560,
                "metadata": {
                    "text": "Sample legal document about property law",
                    "hierarchy_path": "civil_law/property",
                    "source": "lex.uz",
                },
            },
        ]

    @pytest.fixture
    def mock_search_result(self):
        """Create mock search result."""
        mock_hit = Mock()
        mock_hit.id = "doc1"
        mock_hit.score = 0.85
        mock_hit.data = {
            "entity": {
                "text": "Sample legal text",
                "metadata": {"source": "lex.uz", "article_number": "123"},
                "hierarchy_path": "civil_law/contracts",
            }
        }

        return [[mock_hit]]  # Milvus returns nested list structure

    def test_connection_initialization(self, mock_milvus_client):
        """Test Milvus connection initialization."""
        with patch(
            "app.db.milvus_handler.MilvusClient", return_value=mock_milvus_client
        ):
            MilvusHandler()

            # Verify MilvusClient was initialized with correct parameters
            mock_milvus_client.assert_called_once()

            # Verify collections were created for each configured collection
            expected_collections = [
                settings.MILVUS_MAIN_NAME,
                settings.MILVUS_SOLIQ_ASSISTANT_NAME,
                settings.MILVUS_PROJECT_FILES,
            ]

            assert mock_milvus_client.has_collection.call_count == len(
                expected_collections
            )

    def test_collection_creation(self, milvus_handler, mock_milvus_client):
        """Test collection creation process."""
        collection_name = "test_collection"

        # Mock that collection doesn't exist
        mock_milvus_client.has_collection.return_value = False

        result = milvus_handler.create_collection(collection_name)

        # Verify collection was created
        mock_milvus_client.create_collection.assert_called_once()
        mock_milvus_client.create_index.assert_called_once()
        mock_milvus_client.load_collection.assert_called_once_with(collection_name)

        assert result == collection_name

    def test_collection_exists(self, milvus_handler, mock_milvus_client):
        """Test handling of existing collection."""
        collection_name = "existing_collection"

        # Mock that collection exists
        mock_milvus_client.has_collection.return_value = True

        result = milvus_handler.create_collection(collection_name)

        # Verify no creation attempts were made
        mock_milvus_client.create_collection.assert_not_called()
        mock_milvus_client.create_index.assert_not_called()
        mock_milvus_client.load_collection.assert_not_called()

        assert result == collection_name

    def test_upsert_vectors(self, milvus_handler, mock_milvus_client, sample_documents):
        """Test upserting vectors to Milvus."""
        milvus_handler.upsert_vectors(sample_documents)

        # Verify insert was called
        mock_milvus_client.insert.assert_called_once()

        # Get the call arguments
        call_args = mock_milvus_client.insert.call_args
        assert call_args[0][0] == settings.MILVUS_MAIN_NAME  # collection_name

        # Verify data format
        inserted_data = call_args[0][1]  # milvus_data
        assert len(inserted_data) == 2
        assert all("id" in doc for doc in inserted_data)
        assert all("text" in doc for doc in inserted_data)
        assert all("text_dense" in doc for doc in inserted_data)

    def test_upsert_vectors_with_partition(
        self, milvus_handler, mock_milvus_client, sample_documents
    ):
        """Test upserting vectors to specific partition."""
        partition_name = "test_partition"

        milvus_handler.upsert_vectors(sample_documents, partition_name=partition_name)

        # Verify partition creation check was made
        mock_milvus_client.has_partition.assert_called()

        # Verify insert was called with partition
        call_args = mock_milvus_client.insert.call_args
        assert call_args[1]["partition_name"] == partition_name

    def test_query_dense_search(
        self, milvus_handler, mock_milvus_client, mock_search_result
    ):
        """Test dense vector search."""
        test_vector = [0.1] * 2560
        mock_milvus_client.search.return_value = mock_search_result

        results = milvus_handler.query_dense(test_vector, top_k=5)

        # Verify search was called with correct parameters
        mock_milvus_client.search.assert_called_once_with(
            collection_name=settings.MILVUS_MAIN_NAME,
            data=[test_vector],
            anns_field="text_dense",
            search_params={"metric_type": "COSINE"},
            limit=5,
            output_fields=["text", "metadata"],
            partitions=None,
            filter=None,
        )

        # Verify result parsing
        assert len(results) == 1
        assert results[0]["id"] == "doc1"
        assert results[0]["score"] == 0.85
        assert "metadata" in results[0]

    def test_query_hybrid_search(
        self, milvus_handler, mock_milvus_client, mock_search_result
    ):
        """Test hybrid search (dense + sparse)."""
        test_vector = [0.1] * 2560
        text_query = "legal contracts requirements"
        mock_milvus_client.hybrid_search.return_value = mock_search_result

        results = milvus_handler.query_hybrid(
            dense_vector=test_vector, text_query=text_query, top_k=10, alpha=0.7
        )

        # Verify hybrid_search was called
        mock_milvus_client.hybrid_search.assert_called_once()

        # Verify result parsing
        assert len(results) == 1
        assert results[0]["id"] == "doc1"

    def test_query_sparse_search(
        self, milvus_handler, mock_milvus_client, mock_search_result
    ):
        """Test sparse vector search."""
        text_query = "contract law article 123"
        mock_milvus_client.search.return_value = mock_search_result

        results = milvus_handler.query_sparse(text_query, top_k=3)

        # Verify search was called with correct parameters
        mock_milvus_client.search.assert_called_once_with(
            collection_name=settings.MILVUS_MAIN_NAME,
            data=[text_query],
            anns_field="text_sparse",
            search_params={"drop_ratio_search": 0.2},
            limit=3,
            output_fields=["text", "metadata"],
            partitions=None,
        )

        # Verify result parsing
        assert len(results) == 1
        assert results[0]["id"] == "doc1"

    def test_query_soliq_assistant(
        self, milvus_handler, mock_milvus_client, mock_search_result
    ):
        """Test soliq assistant specific search."""
        test_vector = [0.1] * 2560
        mock_milvus_client.search.return_value = mock_search_result

        results = milvus_handler.query_soliq_assistant(test_vector, top_k=5)

        # Verify multiple searches were made (for different filters)
        assert mock_milvus_client.search.call_count == 3  # Three filters

        # Verify result aggregation
        assert len(results) >= 1

    def test_build_like_or_expression(self, milvus_handler):
        """Test building filter expressions for article numbers."""
        values = [123, 456, 789]
        expr = milvus_handler.build_like_or_expr(values)

        expected = (
            'metadata["article_number"] == 123 or '
            'metadata["article_number"] == 456 or '
            'metadata["article_number"] == 789'
        )
        assert expr == expected

    def test_build_like_or_expression_empty(self, milvus_handler):
        """Test building filter expression with empty values."""
        expr = milvus_handler.build_like_or_expr([])
        assert expr == ""

    def test_partition_creation(self, milvus_handler, mock_milvus_client):
        """Test partition creation."""
        partition_name = "test_partition"
        mock_milvus_client.has_partition.return_value = False

        result = milvus_handler.create_partition(partition_name)

        mock_milvus_client.create_partition.assert_called_once_with(
            collection_name=settings.MILVUS_MAIN_NAME, partition_name=partition_name
        )
        assert result == partition_name

    def test_partition_exists(self, milvus_handler, mock_milvus_client):
        """Test handling of existing partition."""
        partition_name = "existing_partition"
        mock_milvus_client.has_partition.return_value = True

        result = milvus_handler.create_partition(partition_name)

        mock_milvus_client.create_partition.assert_not_called()
        assert result == partition_name

    def test_delete_collection(self, milvus_handler, mock_milvus_client):
        """Test collection deletion."""
        mock_milvus_client.has_collection.return_value = True

        milvus_handler.delete_collection("test_collection")

        mock_milvus_client.drop_collection.assert_called_once_with("test_collection")

    def test_delete_nonexistent_collection(self, milvus_handler, mock_milvus_client):
        """Test deletion of non-existent collection."""
        mock_milvus_client.has_collection.return_value = False

        milvus_handler.delete_collection("nonexistent_collection")

        mock_milvus_client.drop_collection.assert_not_called()

    def test_upsert_missing_required_fields(self, milvus_handler, mock_milvus_client):
        """Test upsert with documents missing required fields."""
        invalid_docs = [
            {"id": "doc1"},  # Missing embedding
            {"embedding": [0.1] * 2560},  # Missing id
            {"id": "doc3", "embedding": [0.3] * 2560, "metadata": {}},  # Missing text
        ]

        milvus_handler.upsert_vectors(invalid_docs)

        # Should not attempt insert for invalid documents
        mock_milvus_client.insert.assert_not_called()

    def test_query_with_expression_filter(
        self, milvus_handler, mock_milvus_client, mock_search_result
    ):
        """Test querying with expression filter."""
        test_vector = [0.1] * 2560
        filter_expr = 'metadata["source"] == "lex.uz"'
        mock_milvus_client.search.return_value = mock_search_result

        milvus_handler.query_dense(dense_vector=test_vector, expr=filter_expr)

        # Verify filter was passed correctly
        call_args = mock_milvus_client.search.call_args
        assert call_args[1]["filter"] == filter_expr


if __name__ == "__main__":
    pytest.main([__file__])
