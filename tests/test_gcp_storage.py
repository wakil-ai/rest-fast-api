from unittest.mock import MagicMock, patch

import pytest

from app.services.storage_service import StorageService


@pytest.fixture
def mock_storage_client():
    with patch("app.services.storage_service.storage.Client") as mock_client:
        client_instance = mock_client.return_value
        bucket_mock = MagicMock()
        client_instance.bucket.return_value = bucket_mock
        yield mock_client


@pytest.fixture
def storage_service(mock_storage_client):
    with patch("app.services.storage_service.settings") as mock_settings:
        mock_settings.GCS_BUCKET_NAME = "test-bucket"
        mock_settings.GCS_PROJECT_ID = "test-project"
        mock_settings.GCS_CREDENTIALS_PATH = None

        service = StorageService()
        return service


def test_init(mock_storage_client):
    """Test initialization."""
    with patch("app.services.storage_service.settings") as mock_settings:
        mock_settings.GCS_BUCKET_NAME = "test-bucket"
        mock_settings.GCS_PROJECT_ID = "test-project"
        mock_settings.GCS_CREDENTIALS_PATH = (
            None  # Ensure it doesn't try to load a file
        )

        service = StorageService()

        mock_storage_client.assert_called_with(project="test-project")
        service.client.bucket.assert_called_with("test-bucket")


def test_upload_file_signed(storage_service):
    """Test uploading file and returning signed URL."""
    file_content = b"content"
    path = "uploads/test.txt"

    blob_mock = MagicMock()
    blob_mock.generate_signed_url.return_value = "https://signed.url"
    storage_service.bucket.blob.return_value = blob_mock

    url = storage_service.upload_file(file_content, path, return_signed_url=True)

    assert url == "https://signed.url"
    storage_service.bucket.blob.assert_called_with(path)
    blob_mock.upload_from_string.assert_called_with(
        file_content, content_type="application/octet-stream"
    )
    blob_mock.generate_signed_url.assert_called_once()


def test_upload_file_public(storage_service):
    """Test uploading file and returning public URL."""
    file_content = b"content"
    path = "uploads/public.png"

    blob_mock = MagicMock()
    blob_mock.public_url = "https://public.url"
    storage_service.bucket.blob.return_value = blob_mock

    url = storage_service.upload_file(file_content, path, return_signed_url=False)

    assert url == "https://public.url"
    blob_mock.generate_signed_url.assert_not_called()


def test_get_signed_url(storage_service):
    """Test generating signed URL for existing file."""
    path = "files/doc.pdf"
    blob_mock = MagicMock()
    blob_mock.generate_signed_url.return_value = "https://signed.url"
    storage_service.bucket.blob.return_value = blob_mock

    url = storage_service.get_signed_url(path)

    assert url == "https://signed.url"
    storage_service.bucket.blob.assert_called_with(path)


def test_delete_file_archive(storage_service):
    """Test archiving file (copy then delete source)."""
    path = "files/todelete.txt"
    source_blob = MagicMock()
    storage_service.bucket.blob.return_value = source_blob

    result = storage_service.delete_file(path)

    assert result is True
    # Verify copy was called
    storage_service.bucket.copy_blob.assert_called_once()
    args = storage_service.bucket.copy_blob.call_args[0]
    # args: (source_blob, destination_bucket, new_name)
    assert args[0] == source_blob
    assert args[1] == storage_service.bucket
    assert args[2].startswith("archived/")
    assert args[2].endswith(path)

    # Verify deletion of source
    source_blob.delete.assert_called_once()


def test_permanently_delete_file(storage_service):
    """Test permanent deletion."""
    path = "temp/junk.txt"
    blob_mock = MagicMock()
    storage_service.bucket.blob.return_value = blob_mock

    result = storage_service.permanently_delete_file(path)

    assert result is True
    blob_mock.delete.assert_called_once()


def test_file_exists(storage_service):
    """Test checking existence."""
    path = "check.txt"
    blob_mock = MagicMock()
    blob_mock.exists.return_value = True
    storage_service.bucket.blob.return_value = blob_mock

    assert storage_service.file_exists(path) is True
    blob_mock.exists.assert_called_once()


def test_download_file(storage_service):
    """Test downloading bytes."""
    path = "download.txt"
    blob_mock = MagicMock()
    blob_mock.download_as_bytes.return_value = b"data"
    storage_service.bucket.blob.return_value = blob_mock

    data = storage_service.download_file(path)

    assert data == b"data"
    blob_mock.download_as_bytes.assert_called_once()


def test_get_file_metadata(storage_service):
    """Test retrieving metadata."""
    path = "meta.txt"
    blob_mock = MagicMock()
    blob_mock.size = 100
    blob_mock.content_type = "text/plain"
    # Just checking a few fields
    storage_service.bucket.blob.return_value = blob_mock

    meta = storage_service.get_file_metadata(path)

    assert meta["size"] == 100
    assert meta["content_type"] == "text/plain"
    blob_mock.reload.assert_called_once()


def test_generate_file_path():
    """Test static method for path generation."""
    path = StorageService.generate_file_path("proj1", "file1", "my doc.pdf")
    assert path == "projects/proj1/files/file1/my_doc.pdf"
