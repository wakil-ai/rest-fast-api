import asyncio
import os
from collections.abc import Generator
from unittest.mock import AsyncMock, Mock

import pytest
from fastapi.testclient import TestClient

from app.main import create_app


@pytest.fixture(scope="session")
def event_loop() -> Generator:
    """Create an instance of the default event loop for the test session."""
    loop = asyncio.get_event_loop_policy().new_event_loop()
    yield loop
    loop.close()


@pytest.fixture
def test_client() -> TestClient:
    """Create a test client for the FastAPI app."""
    app = create_app()
    return TestClient(app)


@pytest.fixture
def mock_embedding_response():
    """Mock embedding response for testing."""
    return [0.1] * 1536  # Typical embedding dimension


@pytest.fixture
def mock_milvus_response():
    """Mock Milvus search response for testing."""
    return [
        [
            Mock(
                id="test_doc_1",
                score=0.95,
                data=Mock(
                    entity={
                        "text": "Test document content",
                        "metadata": {"source": "test"},
                    }
                ),
            )
        ]
    ]


@pytest.fixture
def mock_openai_response():
    """Mock OpenAI chat completion response."""
    return "This is a test response from the AI assistant."


@pytest.fixture
def mock_mongo_doc():
    """Mock MongoDB document for testing."""
    return {
        "_id": "507f1f77bcf86cd799439011",
        "user_id": "test_user_123",
        "project_id": "test_project_456",
        "created_at": "2024-01-01T00:00:00Z",
        "content": "Test document content",
    }


@pytest.fixture
def mock_gcs_blob():
    """Mock Google Cloud Storage blob for testing."""
    blob = Mock()
    blob.name = "test/file.txt"
    blob.size = 1024
    blob.content_type = "text/plain"
    blob.public_url = "https://storage.googleapis.com/test-bucket/test/file.txt"
    blob.exists.return_value = True
    blob.download_as_bytes.return_value = b"Test file content"
    return blob


@pytest.fixture
def test_env_vars():
    """Set test environment variables."""
    original_env = os.environ.copy()

    # Set test environment variables
    test_vars = {
        "OPENAI_API_KEY": "test-openai-key",  # pragma: allowlist secret
        "SILICONFLOW_API_KEY": "test-siliconflow-key",  # pragma: allowlist secret
        "MONGODB_URI": "mongodb://localhost:27017/test_db",
        "MONGODB_DB_NAME": "test_wakilai",
        "COLLECTION_NAME": "test_collection",
        "MILVUS_URI": "http://localhost:19530",
        "GCS_PROJECT_ID": "test-project",
        "GCS_BUCKET_NAME": "test-bucket",
        "GCS_CREDENTIALS_PATH": "/path/to/test/credentials.json",
        "DATALAB_API_KEY": "test-datalab-key",  # pragma: allowlist secret
        "DEBUG": "true",
        "API_KEY": "test-api-key",  # pragma: allowlist secret
    }

    os.environ.update(test_vars)
    yield test_vars

    # Restore original environment
    os.environ.clear()
    os.environ.update(original_env)


class MockRequestsResponse:
    """Mock requests.Response for testing HTTP calls."""

    def __init__(self, json_data: dict, status_code: int = 200):
        self.json_data = json_data
        self.status_code = status_code

    def json(self):
        return self.json_data

    def raise_for_status(self):
        if self.status_code >= 400:
            raise Exception(f"HTTP {self.status_code}")


@pytest.fixture
def mock_requests_response():
    """Create a mock requests response."""
    return MockRequestsResponse


# Async test helpers
async def async_none():
    """Helper function for async tests that return None."""
    return None


def create_async_mock(return_value=None):
    """Create an async mock with optional return value."""
    mock = AsyncMock()
    if return_value is not None:
        mock.return_value = return_value
    return mock
