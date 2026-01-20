from fastapi import UploadFile
from typing import Tuple
import uuid
import tempfile
import os

from app.core.logger import logger
from app.core.config import settings

from app.db.db_manager import DBManager
from app.services.storage_service import StorageService
from app.services.ocr_service import OCRService
from app.services.chat_history_service import ChatHistoryService
from app.models.chat_history import FileUploadResponse

class FileManager:
    """Handles file upload, OCR processing, storage and metadata persistence."""

    def __init__(self):
        self.storage = StorageService()
        self.ocr = OCRService()
        self.db = DBManager()
        self.history = ChatHistoryService()
        self.vector_db_collection = settings.MILVUS_PROJECT_FILES

    async def upload_file_to_project(
        self,
        file: UploadFile,
        project_id: str
    ) -> Tuple[int, FileUploadResponse | str]:
        """
        Process file upload: validate → OCR → store → save metadata
        Returns: (status_code, response_or_error_message)
        """
        project = self.history.get_project(project_id)
        if not project:
            return 404, "Project not found"

        content = await file.read()
        file_id = str(uuid.uuid4())

        temp_path = self._create_temp_file(content, file.filename)

        try:
            ocr_result = self.ocr.process_file(temp_path)

            gcs_path = self.storage.generate_file_path(project_id, file_id, file.filename)
            file_url = self.storage.upload_file(
                file_content=content,
                destination_path=gcs_path,
                content_type=file.content_type or "application/octet-stream"
            )

            metadata = self._create_file_metadata(file, content, gcs_path)

            record = self.history.add_file_upload(
                project_id=project_id,
                file_id=file_id,
                file_url=file_url,
                ocr_result=ocr_result,
                file_metadata=metadata,
                status="completed"
            )

            response = self._build_success_response(
                project_id=project_id,
                file_id=file_id,
                metadata=metadata,
                ocr_result=ocr_result,
                record=record
            )

            logger.info("File uploaded successfully: %s → %s", file.filename, project_id)
            return 200, response

        except Exception as e:
            logger.error("File upload failed: %s - %s", file.filename, str(e))
            return 500, str(e)

        finally:
            self._safe_remove_temp_file(temp_path)

    def _upsert_to_vector_db(self, content: str, metadata: dict) -> None:
        """Upsert document to vector database"""
        # TODO splitting logic for the content
        

        self.db.upsert_vectors(document, collection_name=self.vector_db_collection)

    def _create_temp_file(self, content: bytes, original_filename: str) -> str:
        """Create temporary file and return its path"""
        suffix = f"_{original_filename}"
        with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as tmp:
            tmp.write(content)
            return tmp.name

    def _build_success_response(
        self,
        project_id: str,
        file_id: str,
        metadata: dict,
        ocr_result: str,
        record: dict
    ) -> FileUploadResponse:
        """Construct consistent response object"""
        return FileUploadResponse(
            project_id=project_id,
            file_id=file_id,
            file_url=f"/api/history/files/{file_id}/view",
            file_metadata=metadata,
            ocr_result=ocr_result,
            status="completed",
            created_at=record["created_at"],
            updated_at=record["updated_at"],
        )

    @staticmethod
    def _create_file_metadata(
        file: UploadFile,
        content: bytes,
        gcs_path: str
    ) -> dict:
        """Build clean metadata dictionary"""
        return {
            "file_name": file.filename,
            "file_type": file.content_type or "application/octet-stream",
            "file_size": len(content),
            "gcs_path": gcs_path,
        }

    @staticmethod
    def _safe_remove_temp_file(path: str) -> None:
        """Remove temp file if it exists, never raise"""
        try:
            if path and os.path.exists(path):
                os.remove(path)
        except OSError:
            pass  # silent cleanup failure