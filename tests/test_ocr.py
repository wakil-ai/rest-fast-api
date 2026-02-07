from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.services.ocr_service import OCRService


@pytest.fixture
def mock_datalab_client():
    with patch("app.services.ocr_service.AsyncDatalabClient") as mock_client_cls:
        # The client instance is what we interact with
        client_instance = mock_client_cls.return_value

        # Setup convert return value
        # convert() is async, returns a result object with a .markdown attribute
        mock_result = MagicMock()
        mock_result.markdown = "Parsed content"

        # Make convert return a coroutine that returns mock_result
        client_instance.convert = AsyncMock(return_value=mock_result)

        yield mock_client_cls


@pytest.fixture
def ocr_service(mock_datalab_client):
    # Mock settings if needed, or rely on .env/.test.env
    with patch("app.services.ocr_service.settings") as mock_settings:
        mock_settings.DATALAB_API_KEY = "test-ocr-key"
        return OCRService()


def test_init(mock_datalab_client):
    """Test initialization."""
    with patch("app.services.ocr_service.settings") as mock_settings:
        mock_settings.DATALAB_API_KEY = "test-ocr-key"
        service = OCRService()

        mock_datalab_client.assert_called_with(api_key="test-ocr-key")
        assert service.options.output_format == "chunks"
        assert service.options.mode == "balanced"


@pytest.mark.asyncio
async def test_process_file_success(ocr_service):
    """Test processing a file path successfully."""
    file_path = "/path/to/test.pdf"

    result = await ocr_service.process_file(file_path)

    assert result == "Parsed content"

    # Verify client call
    ocr_service.client.convert.assert_called_once()
    kwargs = ocr_service.client.convert.call_args[1]
    assert kwargs["file_path"] == file_path


@pytest.mark.asyncio
async def test_process_url_success(ocr_service):
    """Test processing a URL successfully."""
    url = "http://example.com/doc.pdf"

    result = await ocr_service.process_url(url)

    assert result == "Parsed content"

    # Verify client call
    ocr_service.client.convert.assert_called_once()
    kwargs = ocr_service.client.convert.call_args[1]
    assert kwargs["file_url"] == url


@pytest.mark.asyncio
async def test_process_failure(ocr_service):
    """Test error handling when conversion fails."""
    # Setup failure
    ocr_service.client.convert.side_effect = Exception("API Error")

    with pytest.raises(Exception) as excinfo:
        await ocr_service.process_file("bad_file.pdf")

    assert "API Error" in str(excinfo.value)


@pytest.mark.asyncio
async def test_process_url_failure(ocr_service):
    """Test error handling when URL conversion fails."""
    # Setup failure
    ocr_service.client.convert.side_effect = Exception("Network Error")

    with pytest.raises(Exception) as excinfo:
        await ocr_service.process_url("http://bad-url.com")

    assert "Network Error" in str(excinfo.value)
