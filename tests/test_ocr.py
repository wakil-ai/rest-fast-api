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
    with (
        patch("app.services.ocr_service.settings") as mock_settings,
        patch.object(OCRService, "_build_docling_converter", return_value=MagicMock()),
    ):
        mock_settings.DATALAB_API_KEY = "test-ocr-key"
        mock_settings.FILE_CONTENT_TOKEN_LIMIT = 10000
        return OCRService()


def test_init(mock_datalab_client):
    """Test initialization."""
    with (
        patch("app.services.ocr_service.settings") as mock_settings,
        patch.object(OCRService, "_build_docling_converter", return_value=MagicMock()),
    ):
        mock_settings.DATALAB_API_KEY = "test-ocr-key"
        mock_settings.FILE_CONTENT_TOKEN_LIMIT = 10000
        service = OCRService()

        mock_datalab_client.assert_called_with(api_key="test-ocr-key")
        assert service.options.output_format == "markdown"
        assert service.options.mode == "fast"
        assert service.docling_converter is not None


@pytest.mark.asyncio
async def test_process_file_success(ocr_service):
    """Test processing a file path successfully with Docling."""
    file_path = "/path/to/test.pdf"
    with patch.object(
        ocr_service, "_process_with_docling", AsyncMock(return_value="Docling content")
    ) as mock_docling:
        result = await ocr_service.process_file(file_path)

    assert result == "Docling content"
    mock_docling.assert_awaited_once_with(file_path)
    ocr_service.client.convert.assert_not_called()


@pytest.mark.asyncio
async def test_process_file_falls_back_to_datalab(ocr_service):
    """Test Datalab fallback when Docling fails."""
    file_path = "/path/to/test.pdf"
    with patch.object(
        ocr_service,
        "_process_with_docling",
        AsyncMock(side_effect=Exception("Docling failed")),
    ):
        result = await ocr_service.process_file(file_path)

    assert result == "Parsed content"

    ocr_service.client.convert.assert_called_once()
    kwargs = ocr_service.client.convert.call_args[1]
    assert kwargs["file_path"] == file_path
    assert kwargs["options"] == ocr_service.options


@pytest.mark.asyncio
async def test_doc_file_converts_before_docling_load(ocr_service):
    """Test legacy DOC files are converted before Docling reads them."""
    doc_path = "/path/to/legacy.doc"
    docx_path = "/tmp/legacy.docx"
    temp_dir = MagicMock()

    with (
        patch.object(
            ocr_service,
            "_convert_doc_to_docx",
            AsyncMock(return_value=(docx_path, temp_dir)),
        ) as mock_convert,
        patch.object(
            ocr_service,
            "_load_with_docling_sync",
            return_value="Converted DOC content",
        ) as mock_load,
    ):
        result = await ocr_service._process_with_docling(doc_path)

    assert result == "Converted DOC content"
    mock_convert.assert_awaited_once()
    assert str(mock_convert.await_args.args[0]) == doc_path
    mock_load.assert_called_once_with(docx_path)
    temp_dir.cleanup.assert_called_once()


@pytest.mark.asyncio
async def test_process_url_success(ocr_service):
    """Test processing a URL successfully with Docling."""
    url = "http://example.com/doc.pdf"
    with patch.object(
        ocr_service, "_process_with_docling", AsyncMock(return_value="Docling URL")
    ) as mock_docling:
        result = await ocr_service.process_url(url)

    assert result == "Docling URL"
    mock_docling.assert_awaited_once_with(url)
    ocr_service.client.convert.assert_not_called()


@pytest.mark.asyncio
async def test_process_url_falls_back_to_datalab(ocr_service):
    """Test Datalab URL fallback when Docling fails."""
    url = "http://example.com/doc.pdf"
    with patch.object(
        ocr_service,
        "_process_with_docling",
        AsyncMock(side_effect=Exception("Docling failed")),
    ):
        result = await ocr_service.process_url(url)

    assert result == "Parsed content"

    ocr_service.client.convert.assert_called_once()
    kwargs = ocr_service.client.convert.call_args[1]
    assert kwargs["file_url"] == url
    assert kwargs["options"] == ocr_service.options


@pytest.mark.asyncio
async def test_process_failure(ocr_service):
    """Test error handling when conversion fails."""
    ocr_service.client.convert.side_effect = Exception("API Error")
    with patch.object(
        ocr_service,
        "_process_with_docling",
        AsyncMock(side_effect=Exception("Docling failed")),
    ):
        with pytest.raises(Exception) as excinfo:
            await ocr_service.process_file("bad_file.pdf")

    assert "API Error" in str(excinfo.value)


@pytest.mark.asyncio
async def test_process_url_failure(ocr_service):
    """Test error handling when URL conversion fails."""
    ocr_service.client.convert.side_effect = Exception("Network Error")
    with patch.object(
        ocr_service,
        "_process_with_docling",
        AsyncMock(side_effect=Exception("Docling failed")),
    ):
        with pytest.raises(Exception) as excinfo:
            await ocr_service.process_url("http://bad-url.com")

    assert "Network Error" in str(excinfo.value)
