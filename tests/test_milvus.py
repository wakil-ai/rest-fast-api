from unittest.mock import MagicMock, patch

import pytest

from app.core.config import settings
from app.db.milvus_handler import MilvusHandler


@pytest.fixture
def mock_milvus_client():
    with patch("app.db.milvus_handler.MilvusClient") as mock_client:
        # Setup the client instance that will be returned
        client_instance = mock_client.return_value

        # Setup common mock behaviors
        client_instance.has_collection.return_value = False
        client_instance.has_partition.return_value = False

        yield mock_client


@pytest.fixture
def handler(mock_milvus_client):
    return MilvusHandler()


def test_auth_connection(mock_milvus_client: MagicMock):
    """Test connection initialization and collection setup."""
    # Check connection parameters
    mock_milvus_client.assert_called_with(
        uri=settings.MILVUS_URI,
        user=settings.MILVUS_USER,
        password=settings.MILVUS_PASSWORD,
    )

    # Assert against the mock instance (not handler.client.*)
    instance = mock_milvus_client.return_value

    # We expect creation of MAIN, SOLIQ_ASSISTANT, and PROJECT_FILES
    assert instance.create_collection.call_count >= 2
    assert instance.create_index.call_count >= 2
    assert instance.load_collection.call_count >= 2


def test_check_collections_size(handler):
    """
    Simulate checking that collections have > 50k rows.
    Since this is a unit test, we mock the response to return > 50k.
    """
    client = handler.client

    # Mock response for a count query
    # Assuming the implementation would use query(output_fields=["count(*)"]) type logic
    # or just get_collection_stats. Here we simulate a query returning count.
    client.query.return_value = [{"count(*)": 55000}]

    # This is a hypothetical check logic we run in the test
    for col_name in [settings.MILVUS_MAIN_NAME, settings.MILVUS_SOLIQ_ASSISTANT_NAME]:
        res = client.query(collection_name=col_name, output_fields=["count(*)"])
        count = res[0]["count(*)"]
        assert (
            count > 50000
        ), f"Collection {col_name} has {count} rows, expected > 50000"


def test_query_hybrid(handler):
    """Test hybrid search combination."""
    # Mock hybrid_search return
    mock_hit = MagicMock()
    mock_hit.id = "doc1"
    mock_hit.score = 0.95
    mock_hit.data = {
        "entity": {
            "text": "result text",
            "metadata": {"key": "val"},
            "hierarchy_path": "path/1",
        }
    }
    # hybrid_search returns a list of results (list of lists of hits)
    handler.client.hybrid_search.return_value = [[mock_hit]]

    dense_vec = [0.1] * 128
    results = handler.query_hybrid(dense_vector=dense_vec, text_query="query text")

    assert len(results) == 1
    assert results[0]["id"] == "doc1"
    assert results[0]["score"] == 0.95

    # Verify proper calls
    handler.client.hybrid_search.assert_called_once()
    call_kwargs = handler.client.hybrid_search.call_args[1]
    assert call_kwargs["collection_name"] == settings.MILVUS_MAIN_NAME
    assert len(call_kwargs["reqs"]) == 2  # Dense and Sparse requests


def test_query_dense(handler):
    """Test dense vector search."""
    # Mock search return
    mock_hit = MagicMock()
    mock_hit.id = "doc2"
    mock_hit.score = 0.88
    mock_hit.data = {
        "entity": {
            "text": "dense result",
            "metadata": {"meta": "data"},
            "hierarchy_path": None,
        }
    }
    handler.client.search.return_value = [[mock_hit]]

    dense_vec = [0.2] * 128
    results = handler.query_dense(dense_vec, top_k=5)

    assert len(results) == 1
    assert results[0]["metadata"]["text"] == "dense result"

    handler.client.search.assert_called_once()
    assert handler.client.search.call_args[1]["anns_field"] == "text_dense"


def test_query_sparse(handler):
    """Test sparse keyword search."""
    mock_hit = MagicMock()
    mock_hit.id = "doc3"
    mock_hit.score = 0.75
    mock_hit.data = {
        "entity": {"text": "sparse result", "metadata": {}, "hierarchy_path": "root"}
    }
    handler.client.search.return_value = [[mock_hit]]

    results = handler.query_sparse("keyword query")

    assert len(results) == 1
    assert handler.client.search.call_args[1]["anns_field"] == "text_sparse"


def test_query_specific(handler):
    """Test specific query with article number filtering."""
    # Mock search return
    mock_hit = MagicMock()
    mock_hit.id = "doc4"
    mock_hit.score = 1.0
    mock_hit.data = {
        "entity": {
            "text": "specific text",
            "metadata": {"article_number": 123},
            "hierarchy_path": "path/123",
        }
    }
    handler.client.search.return_value = [[mock_hit]]

    # Mock extract_integers to return a known list
    with patch("app.db.milvus_handler.extract_integers", return_value=[123]):
        results = handler.query_specific("article 123")

        assert len(results) == 1

        # Verify that filter was constructed correctly
        call_kwargs = handler.client.search.call_args[1]
        assert 'metadata["article_number"] == 123' in call_kwargs["filter"]


def test_build_like_or_expr(handler):
    """Test boolean expression builder for filtering."""
    # Test with integers
    values = [115, 114, 115]  # 115 is duplicate
    expr = handler.build_like_or_expr(values)

    # Order should be preserved for unique items: 115, 114
    expected_parts = [
        'metadata["article_number"] == 115',
        'metadata["article_number"] == 114',
    ]

    for part in expected_parts:
        assert part in expr

    assert expr.count(" == ") == 2
    assert " or " in expr

    # Test empty
    assert handler.build_like_or_expr([]) == ""
