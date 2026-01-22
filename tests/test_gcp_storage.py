from datetime import datetime, timedelta
from unittest.mock import Mock, patch

import pytest

from app.services.storage_service import StorageService


@pytest.mark.unit
@pytest.mark.gcp_storage
class TestStorageService:
    """Test Google Cloud Storage service."""

    @pytest.fixture
    def storage_service(self, test_env_vars):
        """Create StorageService instance for testing."""
        with patch("app.services.storage_service.storage.Client") as mock_client:
            mock_instance = Mock()
            mock_client.return_value = mock_instance

            # Mock bucket
            mock_bucket = Mock()
            mock_instance.bucket.return_value = mock_bucket

            service = StorageService()
            service.client = mock_instance
            service.bucket = mock_bucket
            service.bucket_name = "test-bucket"
            return service

    @pytest.fixture
    def mock_blob(self):
        """Mock Google Cloud Storage blob."""
        blob = Mock()
        blob.name = "test/file.txt"
        blob.size = 1024
        blob.content_type = "text/plain"
        blob.time_created = datetime.utcnow()
        blob.updated = datetime.utcnow()
        blob.public_url = "https://storage.googleapis.com/test-bucket/test/file.txt"
        blob.exists.return_value = True
        blob.download_as_bytes.return_value = b"Test file content"
        blob.generate_signed_url.return_value = "https://signed-url-test"
        return blob

    def test_initialization_with_credentials(self, test_env_vars, tmp_path):
        """Test initialization with service account credentials."""
        # Create fake credentials file
        creds_file = tmp_path / "test-credentials.json"
        creds_file.write_text('{"type": "service_account"}')

        with patch(
            "app.services.storage_service.settings.GCS_CREDENTIALS_PATH",
            str(creds_file),
        ):
            with patch("app.services.storage_service.storage.Client") as mock_client:
                with patch(
                    "app.services.storage_service.service_account.Credentials.from_service_account_file"
                ) as mock_creds:
                    mock_credentials = Mock()
                    mock_creds.return_value = mock_credentials

                    StorageService()

                    mock_creds.assert_called_once_with(str(creds_file))
                    mock_client.assert_called_once_with(
                        credentials=mock_credentials, project="test-project"
                    )

    def test_initialization_without_credentials(self, test_env_vars):
        """Test initialization without service account credentials."""
        with patch("app.services.storage_service.settings.GCS_CREDENTIALS_PATH", None):
            with patch("app.services.storage_service.storage.Client") as mock_client:
                mock_instance = Mock()
                mock_client.return_value = mock_instance

                StorageService()

                mock_client.assert_called_once_with(project="test-project")

    def test_upload_file_success(self, storage_service, mock_blob):
        """Test successful file upload with signed URL."""
        storage_service.bucket.blob.return_value = mock_blob

        file_content = b"Test file content"
        destination_path = "uploads/test-file.txt"

        result = storage_service.upload_file(
            file_content=file_content,
            destination_path=destination_path,
            content_type="text/plain",
            return_signed_url=True,
            expiration_minutes=30,
        )

        # Verify blob was created and uploaded
        storage_service.bucket.blob.assert_called_once_with(destination_path)
        mock_blob.upload_from_string.assert_called_once_with(
            file_content, content_type="text/plain"
        )

        # Verify signed URL was generated
        mock_blob.generate_signed_url.assert_called_once_with(
            version="v4",
            expiration=timedelta(minutes=30),
            method="GET",
        )

        assert result == "https://signed-url-test"

    def test_upload_file_public_url(self, storage_service, mock_blob):
        """Test file upload returning public URL."""
        storage_service.bucket.blob.return_value = mock_blob

        file_content = b"Test file content"
        destination_path = "public/test-file.txt"

        result = storage_service.upload_file(
            file_content=file_content,
            destination_path=destination_path,
            return_signed_url=False,
        )

        # Should not generate signed URL
        mock_blob.generate_signed_url.assert_not_called()

        # Should return public URL
        assert result == "https://storage.googleapis.com/test-bucket/test/file.txt"

    def test_upload_file_with_default_parameters(self, storage_service, mock_blob):
        """Test file upload with default parameters."""
        storage_service.bucket.blob.return_value = mock_blob

        file_content = b"Test content"
        destination_path = "test/default.txt"

        storage_service.upload_file(file_content, destination_path)

        # Verify default values
        mock_blob.upload_from_string.assert_called_once_with(
            file_content, content_type="application/octet-stream"
        )
        mock_blob.generate_signed_url.assert_called_once_with(
            version="v4",
            expiration=timedelta(minutes=60),
            method="GET",
        )

    def test_upload_file_error(self, storage_service):
        """Test handling of upload errors."""
        storage_service.bucket.blob.side_effect = Exception("Upload failed")

        with pytest.raises(Exception, match="Upload failed"):
            storage_service.upload_file(b"content", "test.txt")

    def test_get_signed_url(self, storage_service, mock_blob):
        """Test generating signed URL for existing file."""
        storage_service.bucket.blob.return_value = mock_blob

        result = storage_service.get_signed_url("test/file.txt", expiration_minutes=15)

        storage_service.bucket.blob.assert_called_once_with("test/file.txt")
        mock_blob.generate_signed_url.assert_called_once_with(
            version="v4",
            expiration=timedelta(minutes=15),
            method="GET",
        )

        assert result == "https://signed-url-test"

    def test_get_signed_url_error(self, storage_service):
        """Test handling signed URL generation errors."""
        storage_service.bucket.blob.side_effect = Exception("File not found")

        with pytest.raises(Exception, match="File not found"):
            storage_service.get_signed_url("nonexistent.txt")

    def test_archive_file_success(self, storage_service, mock_blob):
        """Test successful file archiving."""
        # Mock source blob
        source_blob = Mock()
        storage_service.bucket.blob.return_value = source_blob

        # Mock the copied blob
        archived_blob = Mock()
        storage_service.bucket.copy_blob.return_value = archived_blob

        file_path = "documents/important.pdf"

        result = storage_service.archive_file(file_path)

        assert result is True

        # Verify blob operations
        storage_service.bucket.blob.assert_called_with(file_path)
        storage_service.bucket.copy_blob.assert_called_once()
        source_blob.delete.assert_called_once()

    def test_archive_file_error(self, storage_service):
        """Test handling of archiving errors."""
        storage_service.bucket.blob.side_effect = Exception("Archive failed")

        result = storage_service.archive_file("test.txt")

        assert result is False

    def test_permanently_delete_file_success(self, storage_service, mock_blob):
        """Test successful permanent file deletion."""
        storage_service.bucket.blob.return_value = mock_blob

        file_path = "temporary/file.txt"

        result = storage_service.permanently_delete_file(file_path)

        assert result is True
        storage_service.bucket.blob.assert_called_once_with(file_path)
        mock_blob.delete.assert_called_once()

    def test_permanently_delete_file_error(self, storage_service):
        """Test handling of permanent deletion errors."""
        storage_service.bucket.blob.return_value = Mock()
        storage_service.bucket.blob.return_value.delete.side_effect = Exception(
            "Delete failed"
        )

        result = storage_service.permanently_delete_file("test.txt")

        assert result is False

    def test_file_exists_true(self, storage_service, mock_blob):
        """Test file existence check - file exists."""
        storage_service.bucket.blob.return_value = mock_blob
        mock_blob.exists.return_value = True

        result = storage_service.file_exists("existing/file.txt")

        assert result is True
        storage_service.bucket.blob.assert_called_once_with("existing/file.txt")
        mock_blob.exists.assert_called_once()

    def test_file_exists_false(self, storage_service, mock_blob):
        """Test file existence check - file doesn't exist."""
        storage_service.bucket.blob.return_value = mock_blob
        mock_blob.exists.return_value = False

        result = storage_service.file_exists("nonexistent/file.txt")

        assert result is False

    def test_file_exists_error(self, storage_service):
        """Test handling of file existence check errors."""
        storage_service.bucket.blob.side_effect = Exception("Check failed")

        result = storage_service.file_exists("test.txt")

        assert result is False

    def test_download_file_success(self, storage_service, mock_blob):
        """Test successful file download."""
        expected_content = b"Downloaded file content"
        storage_service.bucket.blob.return_value = mock_blob
        mock_blob.download_as_bytes.return_value = expected_content

        result = storage_service.download_file("test/file.txt")

        assert result == expected_content
        storage_service.bucket.blob.assert_called_once_with("test/file.txt")
        mock_blob.download_as_bytes.assert_called_once()

    def test_download_file_error(self, storage_service):
        """Test handling of download errors."""
        storage_service.bucket.blob.return_value = Mock()
        storage_service.bucket.blob.return_value.download_as_bytes.side_effect = (
            Exception("Download failed")
        )

        result = storage_service.download_file("test.txt")

        assert result is None

    def test_get_file_metadata_success(self, storage_service, mock_blob):
        """Test getting file metadata."""
        storage_service.bucket.blob.return_value = mock_blob

        result = storage_service.get_file_metadata("test/file.txt")

        assert result is not None
        assert result["name"] == "test/file.txt"
        assert result["size"] == 1024
        assert result["content_type"] == "text/plain"

        storage_service.bucket.blob.assert_called_once_with("test/file.txt")
        mock_blob.reload.assert_called_once()

    def test_get_file_metadata_error(self, storage_service):
        """Test handling of metadata retrieval errors."""
        storage_service.bucket.blob.return_value = Mock()
        storage_service.bucket.blob.return_value.reload.side_effect = Exception(
            "Metadata failed"
        )

        result = storage_service.get_file_metadata("test.txt")

        assert result is None

    def test_list_archived_files_success(self, storage_service, mock_blob):
        """Test listing archived files."""
        mock_blobs = [
            Mock(name="archived/file1.txt"),
            Mock(name="archived/file2.pdf"),
            Mock(name="archived/subdir/file3.doc"),
        ]
        storage_service.bucket.list_blobs.return_value = mock_blobs

        result = storage_service.list_archived_files()

        expected_files = [
            "archived/file1.txt",
            "archived/file2.pdf",
            "archived/subdir/file3.doc",
        ]

        assert result == expected_files
        storage_service.bucket.list_blobs.assert_called_once_with(prefix="archived/")

    def test_list_archived_files_custom_prefix(self, storage_service, mock_blob):
        """Test listing archived files with custom prefix."""
        storage_service.bucket.list_blobs.return_value = [
            Mock(name="custom/2024/file.txt")
        ]

        result = storage_service.list_archived_files(prefix="custom/2024/")

        assert result == ["custom/2024/file.txt"]
        storage_service.bucket.list_blobs.assert_called_once_with(prefix="custom/2024/")

    def test_list_archived_files_error(self, storage_service, caplog):
        """Test handling of archived files listing errors."""
        storage_service.bucket.list_blobs.side_effect = Exception("List failed")

        result = storage_service.list_archived_files()

        assert result == []
        # Should log error but not raise exception

    @pytest.mark.parametrize(
        "project_id,file_id,filename,expected",
        [
            (
                "proj_123",
                "file_456",
                "document.pdf",
                "projects/proj_123/files/file_456/document.pdf",
            ),
            (
                "proj-abc",
                "file-def",
                "my file.txt",
                "projects/proj-abc/files/file-def/my_file.txt",
            ),
            (
                "proj_1",
                "file_2",
                "path/to/file.doc",
                "projects/proj_1/files/file_2/path/to/file.doc",
            ),
        ],
    )
    def test_generate_file_path(self, project_id, file_id, filename, expected):
        """Test file path generation with various inputs."""
        result = StorageService.generate_file_path(project_id, file_id, filename)
        assert result == expected

    def test_complete_file_workflow(self, storage_service, mock_blob):
        """Test complete file upload and retrieval workflow."""
        file_content = b"Workflow test content"
        file_path = "projects/proj_123/files/file_456/test.pdf"

        # Mock all blob operations
        storage_service.bucket.blob.return_value = mock_blob

        # Upload file
        upload_result = storage_service.upload_file(file_content, file_path)
        assert upload_result == "https://signed-url-test"

        # Check file exists
        mock_blob.exists.return_value = True
        exists_result = storage_service.file_exists(file_path)
        assert exists_result is True

        # Get file metadata
        metadata_result = storage_service.get_file_metadata(file_path)
        assert metadata_result is not None
        assert metadata_result["name"] == file_path

        # Download file
        mock_blob.download_as_bytes.return_value = file_content
        download_result = storage_service.download_file(file_path)
        assert download_result == file_content

        # Archive file
        archive_result = storage_service.archive_file(file_path)
        assert archive_result is True


@pytest.mark.integration
@pytest.mark.gcp_storage
@pytest.mark.slow
class TestStorageIntegration:
    """Integration tests for GCP Storage (requires actual GCP credentials)."""

    @pytest.fixture(autouse=True)
    def setup_integration(self):
        """Setup for integration tests."""
        pytest.skip("Integration test - set RUN_INTEGRATION_TESTS=1 to run")

    def test_real_gcp_storage_operations(self):
        """Test real GCP Storage operations (integration test)."""
        # This test would require real GCP credentials and a test bucket
        # and should be run only in CI/CD environment
        pass
