import asyncio
import hashlib
import os
import tempfile

from fastapi import UploadFile
from langchain_text_splitters import RecursiveCharacterTextSplitter
from uuid6 import uuid7

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
from app.utils.tokens import count_tokens


class FileManager:
    """Handles file upload, OCR processing, storage and metadata persistence."""

    def __init__(self):
        self.storage = get_storage_service()
        self.ocr = get_ocr_service()
        self.db = get_db_manager()
        self.history = get_chat_history_service()
        self.embedding_manager = get_embedding_manager()
        self.vector_db_collection = settings.MILVUS_PROJECT_FILES

    async def upload_message_file(
        self, file: UploadFile, user_id: str
    ) -> tuple[int, FileUploadResponse | str]:
        """
        Process file upload for a message: validate → OCR → store → save metadata
        Returns: (status_code, response_or_error_message)
        """
        content = await file.read()
        content_hash = self._build_file_content_hash(content)
        
        existing_by_content = await self.history.get_file_by_content_hash(
            content_hash=content_hash,
            user_id=user_id,
        )
        if existing_by_content:
            existing_file_id = existing_by_content["_id"]
            logger.info(
                f"Reusing existing file by content hash for file_id: {existing_file_id}"
            )
            response = self._build_success_response(
                file_id=existing_file_id,
                metadata=existing_by_content.get("file_metadata", {}),
                ocr_result=existing_by_content.get("ocr_result", ""),
                record=existing_by_content,
                project_id=None,
            )
            return 200, response

        temp_path = self._create_temp_file(content, file.filename)

        try:
            ocr_result = await self.ocr.process_file(temp_path)
            file_id = self._build_deterministic_file_id(ocr_result)

            existing_record = await self.history.get_file_by_id(file_id)
            if existing_record:
                logger.info(
                    f"Reusing existing file record for deterministic file_id: {file_id}"
                )
                response = self._build_success_response(
                    file_id=file_id,
                    metadata=existing_record.get("file_metadata", {}),
                    ocr_result=existing_record.get("ocr_result", ocr_result),
                    record=existing_record,
                    project_id=None,
                )
                return 200, response

            gcs_path = self.storage.generate_message_file_path(
                user_id, file_id, file.filename
            )
            file_url = self.storage.upload_file(
                file_content=content,
                destination_path=gcs_path,
                content_type=file.content_type or "application/octet-stream",
            )

            metadata = self._create_file_metadata(file, content, gcs_path, content_hash)

            record = await self.history.add_file_upload(
                user_id=user_id,
                file_id=file_id,
                file_url=file_url,
                ocr_result=ocr_result,
                file_metadata=metadata,
                status="pending",  # Pending association with a message
                scope="message",
            )

            asyncio.create_task(
                self.index_file(file_id=file_id, ocr_result=ocr_result, record=record)
            )

            response = self._build_success_response(
                file_id=file_id,
                metadata=record.get("file_metadata", metadata),
                ocr_result=ocr_result,
                record=record,
                project_id=None,
            )

            logger.info(
                f"File uploaded successfully: {file.filename}",
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

    async def _upsert_to_vector_db(
        self,
        content: str,
        user_id: str,
        file_id: str,
        file_name: str,
        session_id: str | None = None,
        message_id: str | None = None,
    ) -> int:
        """Chunk content and upsert oversized file context to Milvus."""
        if not content:
            logger.warning(
                f"Empty OCR result for file_id: {file_id}, skipping ingestion"
            )
            return 0

        chunks = self._chunk_file_content(content)

        if not chunks:
            return 0

        embeddings = await self.embedding_manager.aembed_batch(chunks)

        documents = []
        for idx, (chunk_text, embedding) in enumerate(zip(chunks, embeddings)):
            doc_id = f"{file_id}_{idx}"
            metadata = {
                "user_id": user_id,
                "file_id": file_id,
                "file_name": file_name,
                "chunk_index": idx,
            }
            if session_id:
                metadata["session_id"] = session_id
            if message_id:
                metadata["message_id"] = message_id

            documents.append(
                {
                    "id": doc_id,
                    "text": chunk_text,
                    "embedding": embedding,
                    "metadata": metadata,
                }
            )

        try:
            await asyncio.to_thread(
                self.db._upsert_vectors,
                documents,
                self.vector_db_collection,
            )
            logger.info(
                f"Successfully ingested {len(documents)} chunks for file_id: {file_id}"
            )
            return len(documents)
        except Exception as e:
            logger.error(f"Failed to ingest file into Vector DB: {str(e)}")
            raise

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
            status=record.get("status", "completed"),
            created_at=record["created_at"],
            updated_at=record["updated_at"],
        )

    @staticmethod
    def _build_deterministic_file_id(_extracted_text: str) -> str:
        return f"file-{uuid7()}"

    @staticmethod
    def _build_file_content_hash(content: bytes) -> str:
        return hashlib.sha256(content).hexdigest()

    @staticmethod
    def _create_file_metadata(
        file: UploadFile, content: bytes, gcs_path: str, content_hash: str
    ) -> dict:
        """Build clean metadata dictionary"""
        return {
            "file_name": file.filename,
            "file_type": file.content_type or "application/octet-stream",
            "file_size": len(content),
            "gcs_path": gcs_path,
            "file_content_hash": content_hash,
        }

    @staticmethod
    def _safe_remove_temp_file(path: str) -> None:
        """Remove temp file if it exists, never raise"""
        try:
            if path and os.path.exists(path):
                os.remove(path)
        except OSError:
            pass  # silent cleanup failure

    async def index_file(
        self,
        *,
        file_id: str,
        ocr_result: str,
        record: dict,
    ) -> dict:
        token_count = count_tokens(ocr_result)
        if token_count <= settings.FILE_CONTENT_TOKEN_LIMIT:
            return record

        logger.info(
            f"File {file_id} exceeds FILE_CONTENT_TOKEN_LIMIT ({token_count} > "
            f"{settings.FILE_CONTENT_TOKEN_LIMIT}); indexing with Milvus file search"
        )

        try:
            chunk_count = await self._upsert_to_vector_db(
                content=ocr_result,
                user_id=record["user_id"],
                file_id=file_id,
                file_name=record.get("file_metadata", {}).get("file_name", file_id),
                message_id=record.get("message_id"),
            )
            return await self.history.update_file_metadata_fields(
                file_id,
                {
                    "file_metadata.ocr_token_count": token_count,
                    "file_metadata.milvus_file_index.enabled": True,
                    "file_metadata.milvus_file_index.collection": self.vector_db_collection,
                    "file_metadata.milvus_file_index.embedding_model": settings.SILICONFLOW_EMBEDDING_MODEL,
                    "file_metadata.milvus_file_index.chunk_count": chunk_count,
                },
            )
        except Exception as exc:
            logger.warning(
                f"Milvus file indexing failed for file {file_id}; "
                f"falling back to direct OCR context: {exc}",
                exc_info=True,
            )
            return await self.history.update_file_metadata_fields(
                file_id,
                {
                    "file_metadata.ocr_token_count": token_count,
                    "file_metadata.milvus_file_index.enabled": False,
                    "file_metadata.milvus_file_index.error": str(exc),
                },
            )

    @staticmethod
    def _chunk_file_content(content: str) -> list[str]:
        if not content:
            return []

        splitter = RecursiveCharacterTextSplitter.from_tiktoken_encoder(
            model_name="gpt-4.1",
            chunk_size=3000,
            chunk_overlap=1000,
        )
        return [
            chunk.strip()
            for chunk in splitter.split_text(content)
            if chunk.strip()
        ]
