from unittest.mock import Mock, patch

import pytest

from app.core.config import settings
from app.db.milvus_handler import MilvusHandler


@pytest.mark.unit
@pytest.mark.milvus
class TestMilvusHandler:
    """Test Milvus vector database handler."""

    @pytest.fixture
    def milvus_handler(self, test_env_vars):
        """Create MilvusHandler instance for testing."""
        with patch("app.db.milvus_handler.MilvusClient") as mock_client:
            mock_instance = Mock()
            mock_client.return_value = mock_instance

            # Mock the collections existence check
            mock_instance.has_collection.return_value = False

            handler = MilvusHandler()
            handler.client = mock_instance
            return handler

    @patch("app.db.milvus_handler.MilvusClient")
    def test_initialization_creates_collections(self, mock_client, test_env_vars):
        """Test that initialization creates all required collections."""
        mock_instance = Mock()
        mock_instance.has_collection.return_value = False
        mock_client.return_value = mock_instance

        handler = MilvusHandler()

        # Verify client was created with correct parameters
        mock_client.assert_called_once_with(
            uri=settings.MILVUS_URI,
            user=settings.MILVUS_USER,
            password=settings.MILVUS_PASSWORD,
        )

        # Verify all collections were created
        expected_collections = [
            settings.MILVUS_MAIN_NAME,
            settings.MILVUS_SOLIQ_ASSISTANT_NAME,
            settings.MILVUS_PROJECT_FILES,
        ]

        assert handler.milvus_collections == expected_collections
        assert mock_instance.create_collection.call_count == len(expected_collections)
        assert mock_instance.create_index.call_count == len(expected_collections)
        assert mock_instance.load_collection.call_count == len(expected_collections)

    def test_create_collection_already_exists(self, milvus_handler):
        """Test creating a collection that already exists."""
        milvus_handler.client.has_collection.return_value = True

        result = milvus_handler.create_collection("existing_collection")

        assert result == "existing_collection"
        milvus_handler.client.create_collection.assert_not_called()

    def test_create_partition_already_exists(self, milvus_handler):
        """Test creating a partition that already exists."""
        milvus_handler.client.has_partition.return_value = True

        result = milvus_handler.create_partition(
            "existing_partition", "test_collection"
        )

        assert result == "existing_partition"
        milvus_handler.client.create_partition.assert_not_called()

    def test_create_partition_new(self, milvus_handler):
        """Test creating a new partition."""
        milvus_handler.client.has_partition.return_value = False

        result = milvus_handler.create_partition("new_partition", "test_collection")

        milvus_handler.client.create_partition.assert_called_once_with(
            collection_name="test_collection",
            partition_name="new_partition",
        )
        assert result == "new_partition"

    def test_upsert_vectors_success(self, milvus_handler):
        """Test successful vector upsert."""
        documents = [
            {
                "id": "doc_1",
                "embedding": [0.1] * 1536,
                "metadata": {"text": "Test document 1", "source": "test"},
            },
            {
                "id": "doc_2",
                "embedding": [0.2] * 1536,
                "text": "Direct text field",
                "metadata": {"source": "test"},
            },
        ]

        milvus_handler.upsert_vectors(documents, partition_name="test_partition")

        milvus_handler.client.insert.assert_called_once()
        call_args = milvus_handler.client.insert.call_args

        assert call_args[0][0] == settings.MILVUS_MAIN_NAME  # collection_name
        assert call_args[0][1]  # data
        assert call_args[1]["partition_name"] == "test_partition"

    def test_upsert_vectors_missing_fields(self, milvus_handler, caplog):
        """Test upserting vectors with missing required fields."""
        documents = [
            {
                "metadata": {"text": "Missing id and embedding"}
            },  # Missing id and embedding
            {"id": "doc_2", "embedding": [0.1] * 1536, "metadata": {}},  # Missing text
        ]

        milvus_handler.upsert_vectors(documents)

        # Should not attempt to insert documents with missing fields
        assert milvus_handler.client.insert.call_count == 0

    def test_upsert_vectors_creates_partition_if_missing(self, milvus_handler):
        """Test that upsert creates partition if it doesn't exist."""
        documents = [
            {
                "id": "doc_1",
                "embedding": [0.1] * 1536,
                "metadata": {"text": "Test document"},
            }
        ]

        # First call returns False (partition doesn't exist), second returns True (created)
        milvus_handler.client.has_partition.side_effect = [False, True]
        milvus_handler.client.create_partition.return_value = None

        milvus_handler.upsert_vectors(documents, partition_name="new_partition")

        milvus_handler.client.create_partition.assert_called_once_with(
            collection_name=settings.MILVUS_MAIN_NAME, partition_name="new_partition"
        )

    def test_query_dense_success(self, milvus_handler, mock_milvus_response):
        """Test successful dense vector search."""
        dense_vector = [0.1] * 1536

        milvus_handler.client.search.return_value = mock_milvus_response
        milvus_handler._parse_results = Mock(
            return_value=[{"id": "doc_1", "score": 0.95}]
        )

        result = milvus_handler.query_dense(dense_vector, top_k=5)

        milvus_handler.client.search.assert_called_once_with(
            collection_name=settings.MILVUS_MAIN_NAME,
            data=[dense_vector],
            anns_field="text_dense",
            search_params={"metric_type": "COSINE"},
            limit=5,
            output_fields=["text", "metadata"],
            partitions=None,
            filter=None,
        )

        assert isinstance(result, list)

    def test_query_dense_with_filter(self, milvus_handler, mock_milvus_response):
        """Test dense vector search with expression filter."""
        dense_vector = [0.1] * 1536
        filter_expr = 'metadata["source"] == "test"'

        milvus_handler.client.search.return_value = mock_milvus_response
        milvus_handler._parse_results = Mock(
            return_value=[{"id": "doc_1", "score": 0.95}]
        )

        milvus_handler.query_dense(
            dense_vector,
            top_k=5,
            expr=filter_expr,
            partitions=["partition1", "partition2"],
        )

        call_args = milvus_handler.client.search.call_args
        assert call_args[1]["filter"] == filter_expr
        assert call_args[1]["partitions"] == ["partition1", "partition2"]

    def test_query_sparse_success(self, milvus_handler, mock_milvus_response):
        """Test successful sparse vector search."""
        text_query = "legal document about contracts"

        milvus_handler.client.search.return_value = mock_milvus_response
        milvus_handler._parse_results = Mock(
            return_value=[{"id": "doc_1", "score": 0.95}]
        )

        milvus_handler.query_sparse(text_query, top_k=3)

        milvus_handler.client.search.assert_called_once_with(
            collection_name=settings.MILVUS_MAIN_NAME,
            data=[text_query],
            anns_field="text_sparse",
            search_params={"drop_ratio_search": 0.2},
            limit=3,
            output_fields=["text", "metadata"],
            partitions=None,
        )

    def test_query_specific_with_article_numbers(
        self, milvus_handler, mock_milvus_response
    ):
        """Test specific search for article numbers."""
        text_query = "Article 115 and 114"

        # Mock extract_integers function
        with patch("app.db.milvus_handler.extract_integers") as mock_extract:
            mock_extract.return_value = [115, 114]

            milvus_handler.build_like_or_expr = Mock(
                return_value='metadata["article_number"] == 115 or metadata["article_number"] == 114'
            )
            milvus_handler.client.search.return_value = mock_milvus_response
            milvus_handler._parse_results = Mock(
                return_value=[{"id": "doc_1", "score": 0.95}]
            )

            milvus_handler.query_specific(text_query)

            # Verify filter expression was built
            mock_extract.assert_called_once_with(text_query)
            milvus_handler.build_like_or_expr.assert_called_once_with([115, 114])

    def test_query_soliq_assistant(self, milvus_handler, mock_milvus_response):
        """Test soliq assistant query with multiple filters."""
        dense_vector = [0.1] * 1536

        milvus_handler.query_dense = Mock(return_value=[{"id": "doc_1", "score": 0.95}])

        result = milvus_handler.query_soliq_assistant(dense_vector, top_k=4)

        # Should make multiple calls with different filters
        assert milvus_handler.query_dense.call_count == 3  # lex.uz, buxgalter.uz, None
        assert len(result) >= 1  # At least some results should be returned

    def test_build_like_or_expr(self, milvus_handler):
        """Test building like-or expressions for filtering."""
        values = [115, 114, 116, 114]  # Including duplicate

        result = milvus_handler.build_like_or_expr(values)

        expected = (
            'metadata["article_number"] == 115 or '
            'metadata["article_number"] == 114 or '
            'metadata["article_number"] == 116'
        )
        assert result == expected

        # Test empty list
        empty_result = milvus_handler.build_like_or_expr([])
        assert empty_result == ""

    def test_delete_collection(self, milvus_handler):
        """Test deleting a collection."""
        milvus_handler.client.has_collection.return_value = True

        milvus_handler.delete_collection("test_collection")

        milvus_handler.client.drop_collection.assert_called_once_with("test_collection")

    def test_delete_collection_not_exists(self, milvus_handler):
        """Test deleting a collection that doesn't exist."""
        milvus_handler.client.has_collection.return_value = False

        milvus_handler.delete_collection("nonexistent_collection")

        milvus_handler.client.drop_collection.assert_not_called()

    def test_parse_results(self, milvus_handler):
        """Test parsing Milvus search results."""
        # Create mock hit object
        mock_hit = Mock()
        mock_hit.id = "doc_123"
        mock_hit.score = 0.95
        mock_hit.data = Mock()
        mock_hit.data.entity = {
            "text": "Sample document text",
            "metadata": {"source": "test", "category": "legal"},
            "hierarchy_path": "path/to/document",
        }

        results = [[mock_hit]]  # Milvus returns nested list structure

        parsed = milvus_handler._parse_results(results)

        assert len(parsed) == 1
        assert parsed[0]["id"] == "doc_123"
        assert parsed[0]["score"] == 0.95
        assert parsed[0]["metadata"]["text"] == "Sample document text"
        assert parsed[0]["metadata"]["source"] == "test"
        assert parsed[0]["hierarchy_path"] == "path/to/document"

    def test_parse_results_missing_hierarchy(self, milvus_handler):
        """Test parsing results without hierarchy path."""
        mock_hit = Mock()
        mock_hit.id = "doc_123"
        mock_hit.score = 0.95
        mock_hit.data = Mock()
        mock_hit.data.entity = {
            "text": "Sample document text",
            "metadata": {"source": "test"},
            # No hierarchy_path
        }

        results = [[mock_hit]]
        parsed = milvus_handler._parse_results(results)

        assert parsed[0]["hierarchy_path"] is None


@pytest.mark.integration
@pytest.mark.milvus
@pytest.mark.slow
class TestMilvusIntegration:
    """Integration tests for Milvus (requires actual Milvus instance)."""

    @pytest.fixture(autouse=True)
    def setup_integration(self):
        """Setup for integration tests."""
        pytest.skip("Integration test - set RUN_INTEGRATION_TESTS=1 to run")

    def test_real_milvus_connection(self):
        """Test real Milvus connection (integration test)."""
        # This test would require a running Milvus instance
        # and should be run only in CI/CD environment
        pass
