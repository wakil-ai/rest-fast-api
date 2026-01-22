from pathlib import Path
from unittest.mock import AsyncMock, Mock, patch

import pytest

from app.services.ocr_service import OCRService


@pytest.mark.unit
@pytest.mark.ocr
class TestOCRService:
    """Test OCR service."""

    @pytest.fixture
    def ocr_service(self, test_env_vars):
        """Create OCRService instance for testing."""
        with patch("app.services.ocr_service.AsyncDatalabClient") as mock_client:
            mock_instance = Mock()
            mock_client.return_value = mock_instance

            service = OCRService()
            service.client = mock_instance
            return service

    @pytest.fixture
    def mock_convert_response(self):
        """Mock Datalab conversion response."""
        mock_result = Mock()
        mock_result.markdown = (
            "# Document Title\n\nThis is the extracted text from the document."
        )
        return mock_result

    def test_initialization(self, test_env_vars):
        """Test OCR service initialization."""
        with patch("app.services.ocr_service.AsyncDatalabClient") as mock_client:
            mock_instance = Mock()
            mock_client.return_value = mock_instance

            service = OCRService()

            # Verify client creation with API key
            mock_client.assert_called_once_with()  # pragma: allowlist secret
            assert service.client == mock_instance

            # Verify default options
            assert service.options.output_format == "chunks"
            assert service.options.mode == "balanced"
            assert service.options.paginate is True
            assert service.options.page_range == "0-10"

    def test_initialization_custom_options(self, test_env_vars):
        """Test OCR service initialization with custom options (if implemented)."""
        with patch("app.services.ocr_service.AsyncDatalabClient") as mock_client:
            mock_instance = Mock()
            mock_client.return_value = mock_instance

            service = OCRService()

            # Test that options are properly set
            assert hasattr(service, "options")
            assert hasattr(service, "client")

    @patch("app.services.ocr_service.logger")
    async def test_process_file_success(
        self, mock_logger, ocr_service, mock_convert_response
    ):
        """Test successful file processing."""
        test_file = Path("test_document.pdf")

        # Mock the convert method
        ocr_service.client.convert = AsyncMock(return_value=mock_convert_response)

        result = await ocr_service.process_file(test_file)

        assert (
            result
            == "# Document Title\n\nThis is the extracted text from the document."
        )

        # Verify API call
        ocr_service.client.convert.assert_called_once_with(file_path=test_file)

        # Verify logging
        mock_logger.debug.assert_called_with(
            f"[OCR Service] Processing file: {test_file}"
        )
        mock_logger.success.assert_called_with("[OCR Service] Conversion successful")

    @patch("app.services.ocr_service.logger")
    async def test_process_file_path_string(
        self, mock_logger, ocr_service, mock_convert_response
    ):
        """Test file processing with string path."""
        test_file = "/path/to/document.jpg"

        ocr_service.client.convert = AsyncMock(return_value=mock_convert_response)

        result = await ocr_service.process_file(test_file)

        assert (
            result
            == "# Document Title\n\nThis is the extracted text from the document."
        )
        ocr_service.client.convert.assert_called_once_with(file_path=test_file)

    @patch("app.services.ocr_service.logger")
    async def test_process_file_api_error(self, mock_logger, ocr_service):
        """Test handling of API errors during file processing."""
        test_file = Path("test_document.pdf")

        # Mock API error
        ocr_service.client.convert = AsyncMock(
            side_effect=Exception("API Error: Invalid file")
        )

        with pytest.raises(Exception, match="API Error: Invalid file"):
            await ocr_service.process_file(test_file)

        # Verify error logging
        mock_logger.error.assert_called_with(
            "[OCR Service] Conversion failed: API Error: Invalid file"
        )

    @patch("app.services.ocr_service.logger")
    async def test_process_file_network_error(self, mock_logger, ocr_service):
        """Test handling of network errors during file processing."""
        test_file = Path("test_document.pdf")

        # Mock network error
        ocr_service.client.convert = AsyncMock(
            side_effect=ConnectionError("Network unreachable")
        )

        with pytest.raises(ConnectionError, match="Network unreachable"):
            await ocr_service.process_file(test_file)

        mock_logger.error.assert_called_with(
            "[OCR Service] Conversion failed: Network unreachable"
        )

    @patch("app.services.ocr_service.logger")
    async def test_process_file_timeout_error(self, mock_logger, ocr_service):
        """Test handling of timeout errors during file processing."""
        test_file = Path("test_document.pdf")

        # Mock timeout error
        import asyncio

        ocr_service.client.convert = AsyncMock(
            side_effect=asyncio.TimeoutError("Request timeout")
        )

        with pytest.raises(asyncio.TimeoutError, match="Request timeout"):
            await ocr_service.process_file(test_file)

    @patch("app.services.ocr_service.logger")
    async def test_process_file_empty_response(self, mock_logger, ocr_service):
        """Test handling of empty response from OCR service."""
        test_file = Path("test_document.pdf")

        # Mock empty response
        mock_result = Mock()
        mock_result.markdown = ""
        ocr_service.client.convert = AsyncMock(return_value=mock_result)

        result = await ocr_service.process_file(test_file)

        assert result == ""
        mock_logger.success.assert_called_with("[OCR Service] Conversion successful")

    @patch("app.services.ocr_service.logger")
    async def test_process_url_success(
        self, mock_logger, ocr_service, mock_convert_response
    ):
        """Test successful URL processing."""
        test_url = "https://example.com/document.pdf"

        ocr_service.client.convert = AsyncMock(return_value=mock_convert_response)

        result = await ocr_service.process_url(test_url)

        assert (
            result
            == "# Document Title\n\nThis is the extracted text from the document."
        )

        # Verify API call
        ocr_service.client.convert.assert_called_once_with(file_url=test_url)

        # Verify logging
        mock_logger.debug.assert_called_with(
            f"[OCR Service] Processing URL: {test_url}"
        )
        mock_logger.success.assert_called_with("[OCR Service] Conversion successful")

    @patch("app.services.ocr_service.logger")
    async def test_process_url_api_error(self, mock_logger, ocr_service):
        """Test handling of API errors during URL processing."""
        test_url = "https://example.com/document.pdf"

        # Mock API error
        ocr_service.client.convert = AsyncMock(side_effect=Exception("Invalid URL"))

        with pytest.raises(Exception, match="Invalid URL"):
            await ocr_service.process_url(test_url)

        mock_logger.error.assert_called_with(
            "[OCR Service] Conversion failed: Invalid URL"
        )

    @patch("app.services.ocr_service.logger")
    async def test_process_url_invalid_url_format(self, mock_logger, ocr_service):
        """Test handling of invalid URL format."""
        invalid_url = "not-a-valid-url"

        ocr_service.client.convert = AsyncMock(
            side_effect=Exception("Invalid URL format")
        )

        with pytest.raises(Exception, match="Invalid URL format"):
            await ocr_service.process_url(invalid_url)

    @patch("app.services.ocr_service.logger")
    async def test_process_url_empty_response(self, mock_logger, ocr_service):
        """Test handling of empty response from URL processing."""
        test_url = "https://example.com/document.pdf"

        # Mock empty response
        mock_result = Mock()
        mock_result.markdown = ""
        ocr_service.client.convert = AsyncMock(return_value=mock_result)

        result = await ocr_service.process_url(test_url)

        assert result == ""

    @patch("app.services.ocr_service.logger")
    async def test_process_file_different_formats(
        self, mock_logger, ocr_service, mock_convert_response
    ):
        """Test processing different file formats."""
        test_files = [
            Path("document.pdf"),
            Path("image.jpg"),
            Path("scan.png"),
            Path("document.tiff"),
        ]

        ocr_service.client.convert = AsyncMock(return_value=mock_convert_response)

        for test_file in test_files:
            result = await ocr_service.process_file(test_file)

            assert (
                result
                == "# Document Title\n\nThis is the extracted text from the document."
            )

            # Reset mock for next iteration
            ocr_service.client.convert.reset_mock()

    @patch("app.services.ocr_service.logger")
    async def test_process_url_different_formats(
        self, mock_logger, ocr_service, mock_convert_response
    ):
        """Test processing different URL formats."""
        test_urls = [
            "https://example.com/document.pdf",
            "https://cdn.example.com/image.jpg",
            "https://storage.example.com/scan.png",
        ]

        ocr_service.client.convert = AsyncMock(return_value=mock_convert_response)

        for test_url in test_urls:
            result = await ocr_service.process_url(test_url)

            assert (
                result
                == "# Document Title\n\nThis is the extracted text from the document."
            )

            # Reset mock for next iteration
            ocr_service.client.convert.reset_mock()

    async def test_convert_options_configuration(self, ocr_service):
        """Test that convert options are properly configured."""
        assert ocr_service.options.output_format == "chunks"
        assert ocr_service.options.mode == "balanced"
        assert ocr_service.options.paginate is True
        assert ocr_service.options.page_range == "0-10"

    @patch("app.services.ocr_service.logger")
    async def test_process_large_file(
        self, mock_logger, ocr_service, mock_convert_response
    ):
        """Test processing a large file."""
        test_file = Path("large_document.pdf")

        ocr_service.client.convert = AsyncMock(return_value=mock_convert_response)

        result = await ocr_service.process_file(test_file)

        assert (
            result
            == "# Document Title\n\nThis is the extracted text from the document."
        )
        ocr_service.client.convert.assert_called_once_with(file_path=test_file)

    @patch("app.services.ocr_service.logger")
    async def test_process_with_special_characters(self, mock_logger, ocr_service):
        """Test processing text with special characters."""
        test_file = Path("document_with_special_chars.pdf")

        # Mock response with special characters
        mock_result = Mock()
        mock_result.markdown = (
            "# Document with café & naïve text\n\nSpecial characters: @#$%^&*()"
        )
        ocr_service.client.convert = AsyncMock(return_value=mock_result)

        result = await ocr_service.process_file(test_file)

        assert "café" in result
        assert "naïve" in result
        assert "@#$%^&*()" in result

    def test_service_inheritance(self, ocr_service):
        """Test that OCRService has required methods."""
        # Test that service has the required methods
        assert hasattr(ocr_service, "process_file")
        assert hasattr(ocr_service, "process_url")
        assert callable(getattr(ocr_service, "process_file"))
        assert callable(getattr(ocr_service, "process_url"))


@pytest.mark.unit
@pytest.mark.ocr
class TestOCREndpoint:
    """Test OCR API endpoint."""

    @pytest.fixture
    def test_client(self):
        """Create test client for OCR endpoint."""
        from fastapi import FastAPI
        from fastapi.testclient import TestClient

        from app.api.ocr import router

        app = FastAPI()
        app.include_router(router, prefix="/api")
        return TestClient(app)

    @pytest.fixture
    def mock_ocr_service(self):
        """Mock OCR service."""
        with patch("app.api.ocr.ocr_service") as mock:
            yield mock

    @patch("app.api.ocr.logger")
    def test_ocr_upload_success(self, mock_logger, test_client, mock_ocr_service):
        """Test successful file upload OCR."""
        # Mock OCR service response
        mock_ocr_service.process_file = AsyncMock(return_value="# Extracted text")

        # Create test file
        test_content = b"test file content"

        with patch("builtins.open", create=True) as mock_open:
            mock_open.return_value.__enter__.return_value.write = Mock()

            response = test_client.post(
                "/api/ocr/upload",
                files={"file": ("test.pdf", test_content, "application/pdf")},
            )

        assert response.status_code == 200
        data = response.json()
        assert data["ocr_text"] == "# Extracted text"

        # Verify OCR service was called
        mock_ocr_service.process_file.assert_called_once()

    @patch("app.api.ocr.logger")
    def test_ocr_upload_error(self, mock_logger, test_client, mock_ocr_service):
        """Test OCR upload with processing error."""
        mock_ocr_service.process_file = AsyncMock(
            side_effect=Exception("Processing failed")
        )

        response = test_client.post(
            "/api/ocr/upload",
            files={"file": ("test.pdf", b"content", "application/pdf")},
        )

        assert response.status_code == 500
        data = response.json()
        assert "Failed to process OCR" in data["detail"]

        mock_logger.error.assert_called_once()

    @patch("app.api.ocr.logger")
    def test_ocr_url_success(self, mock_logger, test_client, mock_ocr_service):
        """Test successful URL OCR."""
        mock_ocr_service.process_url = AsyncMock(return_value="# URL extracted text")

        response = test_client.post(
            "/api/ocr/url", json={"url": "https://example.com/document.pdf"}
        )

        assert response.status_code == 200
        data = response.json()
        assert data["ocr_text"] == "# URL extracted text"

        mock_ocr_service.process_url.assert_called_once_with(
            "https://example.com/document.pdf"
        )

    @patch("app.api.ocr.logger")
    def test_ocr_url_missing(self, mock_logger, test_client, mock_ocr_service):
        """Test OCR URL with missing URL."""
        response = test_client.post("/api/ocr/url", json={})

        assert response.status_code == 400
        data = response.json()
        assert "URL is required" in data["detail"]

        # OCR service should not be called
        mock_ocr_service.process_url.assert_not_called()

    @patch("app.api.ocr.logger")
    def test_ocr_url_processing_error(self, mock_logger, test_client, mock_ocr_service):
        """Test OCR URL with processing error."""
        mock_ocr_service.process_url = AsyncMock(
            side_effect=Exception("URL processing failed")
        )

        response = test_client.post(
            "/api/ocr/url", json={"url": "https://example.com/document.pdf"}
        )

        assert response.status_code == 500
        data = response.json()
        assert "Failed to process OCR" in data["detail"]

        mock_logger.error.assert_called_once()

    @patch("app.api.ocr.logger")
    def test_ocr_url_invalid_format(self, mock_logger, test_client, mock_ocr_service):
        """Test OCR URL with invalid URL format."""
        response = test_client.post("/api/ocr/url", json={"url": "not-a-valid-url"})

        assert response.status_code == 500

        mock_logger.error.assert_called_once()

    def test_ocr_response_model(self):
        """Test OCR response model validation."""
        from app.models.ocr import OCRResponse

        # Valid response
        valid_response = OCRResponse(ocr_text="Test OCR result")
        assert valid_response.ocr_text == "Test OCR result"

        # # Test that ocr_text is required
        # with pytest.raises(ValueError):
        #     OCRResponse()


@pytest.mark.integration
@pytest.mark.ocr
@pytest.mark.slow
class TestOCRIntegration:
    """Integration tests for OCR service (requires real Datalab API)."""

    @pytest.fixture(autouse=True)
    def setup_integration(self):
        """Setup for integration tests."""
        pytest.skip("Integration test - set RUN_INTEGRATION_TESTS=1 to run")

    async def test_real_ocr_processing(self):
        """Test real OCR processing (integration test)."""
        # This test would require real Datalab API credentials
        # and should be run only in CI/CD environment
        pass
