import os
import warnings
from datetime import datetime, timedelta

from google.cloud import storage
from google.oauth2 import service_account

from core.config import settings
from core.logger import logger

warnings.filterwarnings(
    "ignore", category=FutureWarning, module="google.api_core._python_version_support"
)


class StorageService:
    """
    Service for handling file uploads to Google Cloud Storage.
    """

    def __init__(self):
        """Initialize Google Cloud Storage client."""
        try:
            credentials = self._credentials_from_environment()
            if credentials is not None:
                self.client = storage.Client(
                    credentials=credentials, project=settings.GOOGLE_PROJECT_ID
                )
            elif settings.GOOGLE_APPLICATION_CREDENTIALS and os.path.exists(
                settings.GOOGLE_APPLICATION_CREDENTIALS
            ):
                # Optional compatibility mode for local development.
                credentials = service_account.Credentials.from_service_account_file(
                    settings.GOOGLE_APPLICATION_CREDENTIALS
                )
                self.client = storage.Client(
                    credentials=credentials, project=settings.GOOGLE_PROJECT_ID
                )
            else:
                # Use default credentials (for Cloud Run, GCE, etc.)
                self.client = storage.Client(project=settings.GOOGLE_PROJECT_ID)

            self.bucket_name = settings.GCS_BUCKET_NAME
            self.bucket = self.client.bucket(self.bucket_name)
            logger.info(
                f"[StorageService] Initialized Google Cloud Storage with bucket: {self.bucket_name}"
            )
        except Exception as e:
            logger.error(f"[StorageService] Failed to initialize GCS client: {str(e)}")
            raise

    @staticmethod
    def _credentials_from_environment():
        """Build credentials from separately injected environment variables.

        Keeping each value in its own variable avoids serializing a complete JSON
        document into GitHub Actions and container environment configuration.
        """
        required = {
            "project_id": settings.GOOGLE_PROJECT_ID,
            "private_key_id": settings.GOOGLE_PRIVATE_KEY_ID,
            "private_key": settings.GOOGLE_PRIVATE_KEY,
            "client_email": settings.GOOGLE_CLIENT_EMAIL,
            "client_id": settings.GOOGLE_SERVICE_ACCOUNT_CLIENT_ID,
        }
        environment_names = {
            "project_id": "GOOGLE_PROJECT_ID",
            "private_key_id": "GOOGLE_PRIVATE_KEY_ID",
            "private_key": "GOOGLE_PRIVATE_KEY",
            "client_email": "GOOGLE_CLIENT_EMAIL",
            "client_id": "GOOGLE_SERVICE_ACCOUNT_CLIENT_ID",
        }
        credential_fields = set(required) - {"project_id"}
        configured_credentials = {name for name in credential_fields if required[name]}
        if not configured_credentials:
            return None

        missing = sorted(name for name, value in required.items() if not value)
        if missing:
            raise ValueError(
                "Incomplete Google server identity configuration; missing: "
                + ", ".join(environment_names[name] for name in missing)
            )

        service_account_info = {
            "type": "service_account",
            **required,
            "private_key": required["private_key"].replace("\\n", "\n"),
            "auth_uri": "https://accounts.google.com/o/oauth2/auth",
            "token_uri": "https://oauth2.googleapis.com/token",
            "auth_provider_x509_cert_url": "https://www.googleapis.com/oauth2/v1/certs",
            "client_x509_cert_url": (
                "https://www.googleapis.com/robot/v1/metadata/x509/"
                + required["client_email"].replace("@", "%40")
            ),
            "universe_domain": "googleapis.com",
        }
        return service_account.Credentials.from_service_account_info(
            service_account_info
        )

    def upload_file(
        self,
        data: bytes,
        destination_path: str,
        content_type: str = "application/octet-stream",
        return_signed_url: bool = True,
        expiration_minutes: int = 60,
    ) -> str:
        """
        Upload a file to Google Cloud Storage.

        Args:
            data: Binary content of the file
            destination_path: Path where file will be stored in the bucket (e.g., 'users/user123/file.pdf')
            content_type: MIME type of the file
            return_signed_url: If True, returns a signed URL (recommended). If False, returns public URL
            expiration_minutes: How long the signed URL should be valid (default: 60 minutes)

        Returns:
            Signed URL (private, temporary) or public URL (permanent) based on return_signed_url parameter
        """
        try:
            blob = self.bucket.blob(destination_path)
            blob.upload_from_string(data, content_type=content_type)

            logger.info(f"[StorageService] Uploaded file to: {destination_path}")
            return self._build_access_url(blob, return_signed_url, expiration_minutes)
        except Exception as e:
            logger.error(f"[StorageService] Failed to upload file: {str(e)}")
            raise

    def upload_file_from_path(
        self,
        file_path: str,
        destination_path: str,
        content_type: str = "application/octet-stream",
        return_signed_url: bool = True,
        expiration_minutes: int = 60,
    ) -> str:
        """Upload a local file to Google Cloud Storage without loading it into memory."""
        try:
            blob = self.bucket.blob(destination_path)
            blob.upload_from_filename(file_path, content_type=content_type)

            logger.info(f"[StorageService] Uploaded file to: {destination_path}")
            return self._build_access_url(blob, return_signed_url, expiration_minutes)
        except Exception as e:
            logger.error(f"[StorageService] Failed to upload file: {str(e)}")
            raise

    @staticmethod
    def _build_access_url(
        blob, return_signed_url: bool, expiration_minutes: int
    ) -> str:
        """Return either a signed URL or public URL for an uploaded blob."""
        if return_signed_url:
            signed_url = blob.generate_signed_url(
                version="v4",
                expiration=timedelta(minutes=expiration_minutes),
                method="GET",
            )
            logger.info(
                f"[StorageService] Generated signed URL (expires in {expiration_minutes} minutes)"
            )
            return signed_url

        logger.warning(
            "[StorageService] Returning public URL. Ensure proper permissions are set."
        )
        return blob.public_url

    def get_signed_url(
        self,
        file_path: str,
        expiration_minutes: int = 60,
    ) -> str:
        """
        Generate a signed URL for private file access.

        Args:
            file_path: Path to the file in the bucket
            expiration_minutes: How long the URL should be valid (default: 60 minutes)

        Returns:
            Signed URL for temporary access
        """
        try:
            blob = self.bucket.blob(file_path)
            url = blob.generate_signed_url(
                version="v4",
                expiration=timedelta(minutes=expiration_minutes),
                method="GET",
            )
            return url
        except Exception as e:
            logger.error(f"[StorageService] Failed to generate signed URL: {str(e)}")
            raise

    def get_public_url(self, file_path: str) -> str:
        """Return the public URL form for an object path.

        Note: Access still depends on bucket/object permissions.
        """
        return f"https://storage.googleapis.com/{self.bucket.name}/{file_path}"

    def delete_file(self, file_path: str) -> bool:
        """
        Archive a file by moving it to the 'archived' folder instead of deleting it.

        Args:
            file_path: Path to the file in the bucket

        Returns:
            True if archived successfully, False otherwise
        """
        try:
            # Generate archived path with timestamp
            timestamp = datetime.utcnow().strftime("%Y%m%d_%H%M%S")
            archived_path = f"archived/{timestamp}/{file_path}"

            # Get source blob
            source_blob = self.bucket.blob(file_path)

            # Copy file to archived folder using bucket.copy_blob()
            # This is more efficient and requires fewer permissions than rewrite()
            self.bucket.copy_blob(source_blob, self.bucket, archived_path)

            # Delete the original file
            source_blob.delete()

            logger.info(
                f"[StorageService] Archived file from '{file_path}' to '{archived_path}'"
            )
            return True
        except Exception as e:
            logger.error(f"[StorageService] Failed to archive file: {str(e)}")
            return False

    def permanently_delete_file(self, file_path: str) -> bool:
        """
        Permanently delete a file from Google Cloud Storage without archiving.
        Use with caution - this action cannot be undone.

        Args:
            file_path: Path to the file in the bucket

        Returns:
            True if deleted successfully, False otherwise
        """
        try:
            blob = self.bucket.blob(file_path)
            blob.delete()
            logger.info(f"[StorageService] Permanently deleted file: {file_path}")
            return True
        except Exception as e:
            logger.error(
                f"[StorageService] Failed to permanently delete file: {str(e)}"
            )
            return False

    def file_exists(self, file_path: str) -> bool:
        """
        Check if a file exists in the bucket.

        Args:
            file_path: Path to the file in the bucket

        Returns:
            True if file exists, False otherwise
        """
        try:
            blob = self.bucket.blob(file_path)
            return blob.exists()
        except Exception as e:
            logger.error(f"[StorageService] Error checking file existence: {str(e)}")
            return False

    def download_file(self, file_path: str) -> bytes | None:
        """
        Download a file from Google Cloud Storage.

        Args:
            file_path: Path to the file in the bucket

        Returns:
            File content as bytes, or None if failed
        """
        try:
            blob = self.bucket.blob(file_path)
            content = blob.download_as_bytes()
            logger.info(f"[StorageService] Downloaded file: {file_path}")
            return content
        except Exception as e:
            logger.error(f"[StorageService] Failed to download file: {str(e)}")
            return None

    def get_file_metadata(self, file_path: str) -> dict | None:
        """
        Get metadata for a file in the bucket.

        Args:
            file_path: Path to the file in the bucket

        Returns:
            Dictionary with file metadata
        """
        try:
            blob = self.bucket.blob(file_path)
            blob.reload()
            return {
                "name": blob.name,
                "size": blob.size,
                "content_type": blob.content_type,
                "created": blob.time_created,
                "updated": blob.updated,
                "md5_hash": blob.md5_hash,
            }
        except Exception as e:
            logger.error(f"[StorageService] Failed to get file metadata: {str(e)}")
            return None

    def list_archived_files(self, prefix: str = "archived/") -> list:
        """
        List all files in the archived folder.

        Args:
            prefix: Prefix path to filter archived files (default: "archived/")

        Returns:
            List of archived file paths
        """
        try:
            blobs = self.bucket.list_blobs(prefix=prefix)
            archived_files = [blob.name for blob in blobs]
            logger.info(f"[StorageService] Found {len(archived_files)} archived files")
            return archived_files
        except Exception as e:
            logger.error(f"[StorageService] Failed to list archived files: {str(e)}")
            return []

    @staticmethod
    def generate_project_file_path(project_id: str, file_id: str, filename: str) -> str:
        """
        Generate a structured path for a project file.
        e.g., projects/{project_id}/files/{file_id}/{filename}
        """
        safe_filename = filename.replace(" ", "_").replace("/", "_")
        return f"projects/{project_id}/files/{file_id}/{safe_filename}"

    @staticmethod
    def generate_message_file_path(user_id: str, file_id: str, filename: str) -> str:
        """
        Generate a structured path for a message file.
        e.g., messages/{user_id}/{file_id}/{filename}
        """
        safe_filename = filename.replace(" ", "_").replace("/", "_")
        return f"messages/{user_id}/{file_id}/{safe_filename}"
