from unittest.mock import Mock, patch

import pytest
from fastapi.testclient import TestClient

from app.api.health import router
from app.main import create_app


@pytest.mark.unit
class TestHealthEndpoint:
    """Test health check endpoint."""

    @pytest.fixture
    def test_client(self):
        """Create test client for health endpoint."""
        app = create_app()
        app.include_router(router, prefix="/api")
        return TestClient(app)

    @patch("app.api.health.check_embedding_service")
    @patch("app.api.health.check_milvus_service")
    @patch("app.api.health.check_openai_service")
    @patch("app.api.health.check_mongodb_service")
    @patch("app.api.health.check_gcp_storage_service")
    @patch("app.api.health.check_ocr_service")
    def test_health_all_services_ok(
        self,
        mock_ocr,
        mock_gcp,
        mock_mongodb,
        mock_openai,
        mock_milvus,
        mock_embedding,
        test_client,
    ):
        """Test health endpoint when all services are OK."""
        # Mock all services to return OK status
        mock_embedding.return_value = {"status": "ok", "response_time": "0.100s"}
        mock_milvus.return_value = {"status": "ok", "response_time": "0.050s"}
        mock_openai.return_value = {"status": "ok", "response_time": "0.200s"}
        mock_mongodb.return_value = {"status": "ok", "response_time": "0.030s"}
        mock_gcp.return_value = {"status": "ok", "response_time": "0.080s"}
        mock_ocr.return_value = {"status": "ok", "response_time": "0.060s"}

        response = test_client.get("/api/health")

        assert response.status_code == 200
        data = response.json()

        assert data["status"] == "ok"
        assert "timestamp" in data
        assert "services" in data

        services = data["services"]
        assert services["embedding"]["status"] == "ok"
        assert services["milvus"]["status"] == "ok"
        assert services["openai"]["status"] == "ok"
        assert services["mongodb"]["status"] == "ok"
        assert services["gcp_storage"]["status"] == "ok"
        assert services["ocr"]["status"] == "ok"

    @patch("app.api.health.check_embedding_service")
    @patch("app.api.health.check_milvus_service")
    @patch("app.api.health.check_openai_service")
    @patch("app.api.health.check_mongodb_service")
    @patch("app.api.health.check_gcp_storage_service")
    def test_health_some_services_degraded(
        self,
        mock_gcp,
        mock_mongodb,
        mock_openai,
        mock_milvus,
        mock_embedding,
        test_client,
    ):
        """Test health endpoint when some services are degraded."""
        # Mock some services to fail
        mock_embedding.return_value = {"status": "ok", "response_time": "0.100s"}
        mock_milvus.return_value = Exception("Connection failed")
        mock_openai.return_value = {"status": "error", "error": "API limit exceeded"}
        mock_mongodb.return_value = {"status": "ok", "response_time": "0.030s"}
        mock_gcp.return_value = {"status": "ok", "response_time": "0.080s"}

        response = test_client.get("/api/health")

        assert response.status_code == 503
        data = response.json()

        assert data["status"] == "degraded"

        services = data["services"]
        assert services["embedding"]["status"] == "ok"
        assert services["milvus"]["status"] == "error"
        assert services["openai"]["status"] == "error"
        assert services["mongodb"]["status"] == "ok"
        assert services["gcp_storage"]["status"] == "ok"

    @patch("app.api.health.EmbeddingManager")
    async def test_check_embedding_service_success(self, mock_manager_class):
        """Test embedding service check success."""
        mock_manager = Mock()
        mock_manager_class.return_value = mock_manager
        mock_manager.embed_query.return_value = [0.1] * 1536

        from app.api.health import check_embedding_service

        result = await check_embedding_service()

        assert result["status"] == "ok"
        assert "response_time" in result
        assert result["embedding_dim"] == 1536

    @patch("app.api.health.EmbeddingManager")
    async def test_check_embedding_service_failure(self, mock_manager_class):
        """Test embedding service check failure."""
        mock_manager = Mock()
        mock_manager_class.return_value = mock_manager
        mock_manager.embed_query.side_effect = Exception("API Error")

        from app.api.health import check_embedding_service

        with pytest.raises(Exception):
            await check_embedding_service()

    @patch("app.api.health.MilvusHandler")
    async def test_check_milvus_service_success(self, mock_handler_class):
        """Test Milvus service check success."""
        mock_handler = Mock()
        mock_handler_class.return_value = mock_handler
        mock_handler.client.has_collection.return_value = True
        mock_handler.milvus_collections = ["main", "soliq", "projects"]

        from app.api.health import check_milvus_service

        result = await check_milvus_service()

        assert result["status"] == "ok"
        assert "response_time" in result
        assert result["collections"] == ["main", "soliq", "projects"]

    @patch("app.api.health.MilvusHandler")
    async def test_check_milvus_service_no_collection(self, mock_handler_class):
        """Test Milvus service check when main collection doesn't exist."""
        mock_handler = Mock()
        mock_handler_class.return_value = mock_handler
        mock_handler.client.has_collection.return_value = False

        from app.api.health import check_milvus_service

        result = await check_milvus_service()

        assert result["status"] == "error"
        assert "Main collection not found" in result["error"]

    @patch("app.api.health.ChatGPT")
    async def test_check_openai_service_success(self, mock_chatgpt_class):
        """Test OpenAI service check success."""
        mock_handler = Mock()
        mock_chatgpt_class.return_value = mock_handler
        mock_handler._generate_complete.return_value = "Hello! How can I help you?"
        mock_handler.model = "gpt-4"

        from app.api.health import check_openai_service

        result = await check_openai_service()

        assert result["status"] == "ok"
        assert "response_time" in result
        assert result["model"] == "gpt-4"
        assert result["response_length"] > 0

    @patch("app.api.health.ChatGPT")
    async def test_check_openai_service_empty_response(self, mock_chatgpt_class):
        """Test OpenAI service check with empty response."""
        mock_handler = Mock()
        mock_chatgpt_class.return_value = mock_handler
        mock_handler._generate_complete.return_value = ""
        mock_handler.model = "gpt-4"

        from app.api.health import check_openai_service

        result = await check_openai_service()

        assert result["status"] == "error"
        assert "Empty response from LLM" in result["error"]

    @patch("app.api.health.MongoHandler")
    async def test_check_mongodb_service_success(self, mock_handler_class):
        """Test MongoDB service check success."""
        mock_handler = Mock()
        mock_handler_class.return_value = mock_handler
        mock_handler.client.admin.command.return_value = {"ok": 1}
        mock_handler.client.__getitem__.return_value.list_collection_names.return_value = [
            "users",
            "projects",
            "chats",
        ]

        from app.api.health import check_mongodb_service

        result = await check_mongodb_service()

        assert result["status"] == "ok"
        assert "response_time" in result
        assert result["collections_count"] == 3

    @patch("app.api.health.StorageService")
    async def test_check_gcp_storage_service_success(self, mock_storage_class):
        """Test GCP Storage service check success."""
        mock_service = Mock()
        mock_storage_class.return_value = mock_service
        mock_service.bucket_name = "test-bucket"
        mock_bucket = Mock()
        mock_service.bucket = mock_bucket
        mock_bucket.reload.return_value = None

        from app.api.health import check_gcp_storage_service

        result = await check_gcp_storage_service()

        assert result["status"] == "ok"
        assert "response_time" in result
        assert result["bucket_name"] == "test-bucket"

    @patch("app.api.health.StorageService")
    async def test_check_gcp_storage_service_failure(self, mock_storage_class):
        """Test GCP Storage service check failure."""
        mock_service = Mock()
        mock_storage_class.return_value = mock_service
        mock_service.bucket = Mock()
        mock_service.bucket.reload.side_effect = Exception("Bucket access denied")

        from app.api.health import check_gcp_storage_service

        with pytest.raises(Exception):
            await check_gcp_storage_service()

    @patch("app.api.health.OCRService")
    async def test_check_ocr_service_success(self, mock_ocr_service_class):
        """Test OCR service check success."""
        mock_handler = Mock()
        mock_ocr_service_class.return_value = mock_handler
        mock_handler.client = Mock()

        from app.api.health import check_ocr_service

        result = await check_ocr_service()

        assert result["status"] == "ok"
        assert "response_time" in result
        assert result["api_key_configured"] is True

    @patch("app.api.health.OCRService")
    async def test_check_ocr_service_failure(self, mock_ocr_service_class):
        """Test OCR service check failure."""
        mock_handler = Mock()
        mock_handler.client = None
        mock_ocr_service_class.return_value = mock_handler

        from app.api.health import check_ocr_service

        result = await check_ocr_service()

        assert result["status"] == "error"
        assert "OCR client not initialized" in result["error"]

    @patch("app.api.health.OCRService")
    async def test_check_ocr_service_exception(self, mock_ocr_service_class):
        """Test OCR service check with exception."""
        mock_ocr_service_class.side_effect = Exception("Initialization failed")

        from app.api.health import check_ocr_service

        with pytest.raises(Exception):
            await check_ocr_service()

    def test_health_endpoint_without_authentication(self, test_client):
        """Test that health endpoint doesn't require authentication."""
        response = test_client.get("/api/health")

        # Should not require authentication (no 401 Unauthorized)
        assert response.status_code != 401
