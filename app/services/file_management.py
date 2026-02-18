import os
import tempfile
import uuid

from fastapi import UploadFile

from app.core.config import settings
from app.core.dependencies import (
    get_chat_history_service,
    get_db_manager,
    get_embedding_manager,
    get_ocr_service,
    get_storage_service,
)
from app.core.logger import logger
from app.models.chat_history import FileUploadResponse


class FileManager:
    """Handles file upload, OCR processing, storage and metadata persistence."""

    def __init__(self):
        self.storage = get_storage_service()
        self.ocr = get_ocr_service()
        self.db = get_db_manager()
        self.history = get_chat_history_service()
        self.embedding_manager = get_embedding_manager()
        self.vector_db_collection = settings.MILVUS_PROJECT_FILES

    async def upload_file_to_project(
        self, file: UploadFile, project_id: str, user_id: str
    ) -> tuple[int, FileUploadResponse | str]:
        """
        Process file upload for a project: validate → OCR → store → save metadata
        Returns: (status_code, response_or_error_message)
        """
        project = await self.history.get_project(project_id)
        if not project:
            return 404, "Project not found"

        content = await file.read()
        file_id = str(uuid.uuid4())

        temp_path = self._create_temp_file(content, file.filename)

        try:
            ocr_result = await self.ocr.process_file(temp_path)

            gcs_path = self.storage.generate_project_file_path(
                project_id, file_id, file.filename
            )
            file_url = self.storage.upload_file(
                file_content=content,
                destination_path=gcs_path,
                content_type=file.content_type or "application/octet-stream",
            )

            metadata = self._create_file_metadata(file, content, gcs_path)

            record = await self.history.add_file_upload(
                user_id=user_id,
                file_id=file_id,
                file_url=file_url,
                ocr_result=ocr_result,
                file_metadata=metadata,
                status="completed",
                scope="project",
                project_id=project_id,
            )

            self._upsert_to_vector_db(
                content=ocr_result,
                project_id=project_id,
                user_id=user_id,
                file_id=file_id,
                file_name=file.filename,
            )

            response = self._build_success_response(
                project_id=project_id,
                file_id=file_id,
                metadata=metadata,
                ocr_result=ocr_result,
                record=record,
            )

            logger.info(
                "File uploaded and ingested successfully: %s → %s",
                file.filename,
                project_id,
            )
            return 200, response

        except Exception as e:
            logger.error(
                "File upload failed: %s - %s", file.filename, str(e), exc_info=True
            )
            return 500, str(e)

        finally:
            self._safe_remove_temp_file(temp_path)

    async def upload_message_file(
        self, file: UploadFile, user_id: str
    ) -> tuple[int, FileUploadResponse | str]:
        """
        Process file upload for a message: validate → OCR → store → save metadata
        Returns: (status_code, response_or_error_message)
        """
        content = await file.read()
        file_id = str(uuid.uuid4())

        temp_path = self._create_temp_file(content, file.filename)

        try:
            ocr_result = await self.ocr.process_file(temp_path)

            gcs_path = self.storage.generate_message_file_path(
                user_id, file_id, file.filename
            )
            file_url = self.storage.upload_file(
                file_content=content,
                destination_path=gcs_path,
                content_type=file.content_type or "application/octet-stream",
            )

            metadata = self._create_file_metadata(file, content, gcs_path)

            record = await self.history.add_file_upload(
                user_id=user_id,
                file_id=file_id,
                file_url=file_url,
                ocr_result=ocr_result,
                file_metadata=metadata,
                status="pending",  # Pending association with a message
                scope="message",
            )

            # Note: Vector ingestion can be triggered here or after association
            # For now, we'll skip it until the file is associated with a message

            response = self._build_success_response(
                file_id=file_id,
                metadata=metadata,
                ocr_result=ocr_result,
                record=record,
                project_id=None,
            )

            logger.info(
                "File uploaded successfully, pending message association: %s",
                file.filename,
            )
            return 200, response

        except Exception as e:
            logger.error(
                f"Message file upload failed: {file.filename} - {str(e)}",
                exc_info=True,
            )
            return 500, str(e)

        finally:
            self._safe_remove_temp_file(temp_path)

    def _upsert_to_vector_db(
        self,
        content: str,
        user_id: str,
        file_id: str,
        file_name: str,
        project_id: str | None = None,
        session_id: str | None = None,
    ) -> None:
        """Chunk content and upsert to vector database with metadata."""
        if not content or not project_id:  # Require project_id for vector ingestion
            logger.warning(
                "Empty OCR result for file_id: %s, skipping ingestion", file_id
            )
            return

        # Simple chunking logic (could be improved with a dedicated splitter)
        chunk_size = 1000
        overlap = 100
        chunks = []

        for i in range(0, len(content), chunk_size - overlap):
            chunk_text = content[i : i + chunk_size]
            if chunk_text:
                chunks.append(chunk_text)

        if not chunks:
            return

        # Generate embeddings and prepare documents for Milvus
        embeddings = self.embedding_manager.embed_batch(chunks)

        documents = []
        for idx, (chunk_text, embedding) in enumerate(zip(chunks, embeddings)):
            doc_id = f"{file_id}_{idx}"
            metadata = {
                "user_id": user_id,
                "file_id": file_id,
                "file_name": file_name,
                "chunk_index": idx,
            }
            if project_id:
                metadata["project_id"] = project_id
            if session_id:
                metadata["session_id"] = session_id

            documents.append(
                {
                    "id": doc_id,
                    "text": chunk_text,
                    "embedding": embedding,
                    "metadata": metadata,
                }
            )

        try:
            self.db._upsert_vectors(
                documents, collection_name=self.vector_db_collection
            )
            logger.info(
                "Successfully ingested %d chunks for file_id: %s",
                len(documents),
                file_id,
            )
        except Exception as e:
            logger.error("Failed to ingest file into Vector DB: %s", str(e))

    def _create_temp_file(self, content: bytes, original_filename: str) -> str:
        """Create temporary file and return its path"""
        suffix = f"_{original_filename}"
        with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as tmp:
            tmp.write(content)
            return tmp.name

    def _build_success_response(
        self,
        file_id: str,
        metadata: dict,
        ocr_result: str,
        record: dict,
        project_id: str | None = None,
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
    def _create_file_metadata(file: UploadFile, content: bytes, gcs_path: str) -> dict:
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
