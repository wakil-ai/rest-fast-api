import asyncio
import hashlib
import os
import tempfile

from fastapi import UploadFile
from uuid6 import uuid7

from app.core.config import settings
from app.core.dependencies import (
    get_chat_history_service,
    get_llm_service_client,
    get_storage_service,
)
from app.core.logger import logger
from app.models.chat_history import FileUploadResponse
from app.utils.progress_webhook import send_project_file_progress_webhook
from app.utils.tokens import count_tokens


class FileManager:
    """Handles file upload, OCR processing, storage and metadata persistence."""

    def __init__(self):
        self.storage = get_storage_service()
        self.history = get_chat_history_service()

    async def _process_ocr(self, temp_path: str, *, filename: str, content_type: str) -> str:
        with open(temp_path, "rb") as file_obj:
            result = await get_llm_service_client().ocr_file(
                file=file_obj,
                filename=filename,
                content_type=content_type,
            )
        return str(result.get("ocr_text") or "")

    async def upload_message_file(
        self, file: UploadFile, user_id: str
    ) -> tuple[int, FileUploadResponse | str]:
        """
        Process file upload for a message: validate → OCR → store → save metadata
        Returns: (status_code, response_or_error_message)
        """
        temp_path, content_hash, file_size = await self._spool_upload_to_temp(
            file, file.filename or "upload"
        )

        try:
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

            ocr_result = await self._process_ocr(
                temp_path,
                filename=file.filename or "upload",
                content_type=file.content_type or "application/octet-stream",
            )
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
                user_id, file_id, file.filename or "upload"
            )
            file_url = self.storage.upload_file_from_path(
                file_path=temp_path,
                destination_path=gcs_path,
                content_type=file.content_type or "application/octet-stream",
            )

            metadata = self._create_file_metadata(
                file, file_size, gcs_path, content_hash
            )

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

    async def upload_project_file(
        self,
        file: UploadFile,
        user_id: str,
        project_id: str,
        webhook_url: str | None = None,
        session_id: str | None = None,
    ) -> tuple[int, FileUploadResponse | str]:
        """
        Upload a document into a project: OCR (webhook 25%), then async chunking,
        embeddings (50%), indexed file context with ``project_id`` (100%).
        """
        from app.core.dependencies import get_project_service

        project_service = get_project_service()
        await project_service.get_project(project_id, user_id)

        temp_path, content_hash, file_size = await self._spool_upload_to_temp(
            file, file.filename or "upload"
        )

        try:
            existing_by_content = (
                await self.history.get_file_by_content_hash_for_project(
                    content_hash=content_hash,
                    user_id=user_id,
                    project_id=project_id,
                )
            )
            if existing_by_content:
                fid = existing_by_content["_id"]
                response = self._build_success_response(
                    file_id=fid,
                    metadata=existing_by_content.get("file_metadata", {}),
                    ocr_result=existing_by_content.get("ocr_result", ""),
                    record=existing_by_content,
                    project_id=project_id,
                )
                return 200, response

            ocr_result = await self._process_ocr(
                temp_path,
                filename=file.filename or "upload",
                content_type=file.content_type or "application/octet-stream",
            )
            file_id = self._build_deterministic_file_id(ocr_result)
            gcs_path = self.storage.generate_project_file_path(
                project_id, file_id, file.filename or "upload"
            )
            file_url = self.storage.upload_file_from_path(
                file_path=temp_path,
                destination_path=gcs_path,
                content_type=file.content_type or "application/octet-stream",
            )
            metadata = self._create_file_metadata(
                file, file_size, gcs_path, content_hash
            )

            record = await self.history.add_file_upload(
                user_id=user_id,
                file_id=file_id,
                file_url=file_url,
                ocr_result=ocr_result,
                file_metadata=metadata,
                status="processing",
                scope="project",
                project_id=project_id,
                session_id=session_id,
                webhook_url=webhook_url,
            )

            await project_service.append_file_id(project_id, file_id)

            await send_project_file_progress_webhook(
                webhook_url or "",
                project_id=project_id,
                file_id=file_id,
                stage="ocr_complete",
                progress_percent=25,
                status="processing",
            )

            asyncio.create_task(
                self._finalize_project_file_ingestion(
                    file_id=file_id,
                    project_id=project_id,
                    user_id=user_id,
                    ocr_result=ocr_result,
                    record=record,
                    webhook_url=webhook_url,
                    file_name=metadata.get("file_name") or file_id,
                )
            )

            response = self._build_success_response(
                file_id=file_id,
                metadata=record.get("file_metadata", metadata),
                ocr_result=ocr_result,
                record=record,
                project_id=project_id,
            )
            logger.info(
                f"Project file upload accepted: {file.filename} project={project_id}"
            )
            return 200, response

        except Exception as e:
            logger.error(
                f"Project file upload failed: {file.filename} - {str(e)}",
                exc_info=True,
            )
            return 500, str(e)

        finally:
            self._safe_remove_temp_file(temp_path)

    async def delete_project_file(
        self,
        project_id: str,
        file_id: str,
        user_id: str,
    ) -> tuple[int, str | None]:
        """Remove a project-scoped file from Mongo/index metadata and the project record."""
        from app.core.dependencies import get_project_service

        project_service = get_project_service()
        await project_service.get_project(project_id, user_id)

        file_record = await self.history.get_file_by_id(file_id)
        if not file_record:
            return 404, "File not found"
        if file_record.get("project_id") != project_id:
            return 404, "File not found"
        if file_record.get("scope") != "project":
            return 400, "File is not scoped to a project"

        # No need to delete GCS file
        # gcs_path = file_record.get("file_metadata", {}).get("gcs_path")
        # if gcs_path:
        #     try:
        #         self.storage.delete_file(gcs_path)
        #     except Exception as exc:
        #         logger.warning(
        #             f"Could not archive GCS file {gcs_path} for project={project_id}: {exc}"
        #         )

        try:
            await get_llm_service_client().delete_vectors(
                {"filter_expr": f'metadata["file_id"] == "{file_id}"'}
            )
        except Exception as exc:
            logger.warning(
                f"Could not delete indexed vectors for project file {file_id}: {exc}"
            )

        await self.history.delete_file_upload(file_id)
        await project_service.remove_file_id(project_id, file_id)
        await project_service.increment_stat(project_id, "docs", -1)

        logger.info(
            f"Deleted project file {file_id} from project {project_id} for user {user_id}"
        )
        return 204, None

    async def _finalize_project_file_ingestion(
        self,
        *,
        file_id: str,
        project_id: str,
        user_id: str,
        ocr_result: str,
        record: dict,
        webhook_url: str | None,
        file_name: str,
    ) -> None:
        from app.core.dependencies import get_project_service

        project_service = get_project_service()
        hook = webhook_url or ""

        try:
            text = (ocr_result or "").strip()
            if not text:
                await self.history.update_file_metadata_fields(
                    file_id,
                    {
                        "file_metadata.milvus_file_index.enabled": False,
                        "file_metadata.milvus_file_index.reason": "empty_ocr",
                    },
                )
                await self.history.update_file_status(file_id, "completed")
                await send_project_file_progress_webhook(
                    hook,
                    project_id=project_id,
                    file_id=file_id,
                    stage="ingestion_skipped",
                    progress_percent=100,
                    status="completed",
                    extra={"reason": "empty_ocr"},
                )
                return

            embed_result = await get_llm_service_client().embed_file(
                {
                    "file_id": file_id,
                    "user_id": user_id,
                    "project_id": project_id,
                    "session_id": record.get("session_id"),
                    "file_name": file_name,
                    "ocr_text": text,
                    "force_index": True,
                }
            )
            chunk_count = int(embed_result.get("chunk_count") or 0)
            if embed_result.get("skipped"):
                await self.history.update_file_metadata_fields(
                    file_id,
                    {
                        "file_metadata.milvus_file_index.enabled": False,
                        "file_metadata.milvus_file_index.reason": embed_result.get("reason"),
                    },
                )
                await self.history.update_file_status(file_id, "completed")
                await send_project_file_progress_webhook(
                    hook,
                    project_id=project_id,
                    file_id=file_id,
                    stage="ingestion_skipped",
                    progress_percent=100,
                    status="completed",
                    extra={"reason": embed_result.get("reason")},
                )
                return

            await send_project_file_progress_webhook(
                hook,
                project_id=project_id,
                file_id=file_id,
                stage="embedding_complete",
                progress_percent=50,
                status="processing",
                extra={"chunk_count": chunk_count},
            )
            token_count = count_tokens(ocr_result)
            await self.history.update_file_metadata_fields(
                file_id,
                {
                    "file_metadata.ocr_token_count": token_count,
                    "file_metadata.milvus_file_index.enabled": True,
                    "file_metadata.milvus_file_index.collection": embed_result.get("collection")
                    or settings.PROJECT_FILES_INDEX_NAME,
                    "file_metadata.milvus_file_index.embedding_model": embed_result.get("embedding_model")
                    or settings.LLM_SERVICE_EMBEDDING_MODEL_NAME,
                    "file_metadata.milvus_file_index.chunk_count": chunk_count,
                },
            )
            await self.history.update_file_status(file_id, "completed")
            await send_project_file_progress_webhook(
                hook,
                project_id=project_id,
                file_id=file_id,
                stage="ingestion_complete",
                progress_percent=100,
                status="completed",
                extra={"chunk_count": chunk_count},
            )
            await project_service.increment_stat(project_id, "docs", 1)

        except Exception as exc:
            logger.error(
                f"Project file ingestion failed file_id={file_id}: {exc}",
                exc_info=True,
            )
            try:
                await self.history.update_file_status(file_id, "failed")
            except Exception:
                pass
            await send_project_file_progress_webhook(
                hook,
                project_id=project_id,
                file_id=file_id,
                stage="failed",
                progress_percent=100,
                status="failed",
                extra={"error": str(exc)},
            )

    async def _upsert_to_vector_db(
        self,
        content: str,
        user_id: str,
        file_id: str,
        file_name: str,
        session_id: str | None = None,
        message_id: str | None = None,
        project_id: str | None = None,
    ) -> int:
        """Ask the internal LLM service to chunk and index uploaded file context."""
        if not content:
            logger.warning(
                f"Empty OCR result for file_id: {file_id}, skipping ingestion"
            )
            return 0

        try:
            result = await get_llm_service_client().embed_file(
                {
                    "file_id": file_id,
                    "user_id": user_id,
                    "file_name": file_name,
                    "ocr_text": content,
                    "session_id": session_id,
                    "message_id": message_id,
                    "project_id": project_id,
                    "force_index": bool(project_id),
                }
            )
            chunk_count = int(result.get("chunk_count") or 0)
            logger.info(
                f"Successfully ingested {chunk_count} chunks for file_id: {file_id}"
            )
            return chunk_count
        except Exception as e:
            logger.error(f"Failed to ingest file into Vector DB: {str(e)}")
            raise

    async def _spool_upload_to_temp(
        self, file: UploadFile, original_filename: str
    ) -> tuple[str, str, int]:
        """Stream an upload to disk while hashing and counting bytes."""
        digest = hashlib.sha256()
        file_size = 0
        temp_path = ""

        try:
            with tempfile.NamedTemporaryFile(
                delete=False, suffix=self._build_temp_suffix(original_filename)
            ) as tmp:
                temp_path = tmp.name
                while chunk := await file.read(1024 * 1024):
                    digest.update(chunk)
                    file_size += len(chunk)
                    tmp.write(chunk)
        except Exception:
            self._safe_remove_temp_file(temp_path)
            raise

        return temp_path, digest.hexdigest(), file_size

    @staticmethod
    def _build_temp_suffix(original_filename: str) -> str:
        """Build a safe temporary-file suffix from an upload filename."""
        safe_filename = os.path.basename(original_filename or "upload")
        safe_filename = safe_filename.replace("/", "_").replace("\\", "_")
        return f"_{safe_filename}"

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
    def _create_file_metadata(
        file: UploadFile, file_size: int, gcs_path: str, content_hash: str
    ) -> dict:
        """Build clean metadata dictionary"""
        return {
            "file_name": file.filename,
            "file_type": file.content_type or "application/octet-stream",
            "file_size": file_size,
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
            f"{settings.FILE_CONTENT_TOKEN_LIMIT}); indexing with internal file search"
        )

        try:
            chunk_count = await self._upsert_to_vector_db(
                content=ocr_result,
                user_id=record["user_id"],
                file_id=file_id,
                file_name=record.get("file_metadata", {}).get("file_name", file_id),
                message_id=record.get("message_id"),
                session_id=record.get("session_id"),
                project_id=record.get("project_id"),
            )
            return await self.history.update_file_metadata_fields(
                file_id,
                {
                    "file_metadata.ocr_token_count": token_count,
                    "file_metadata.milvus_file_index.enabled": True,
                    "file_metadata.milvus_file_index.collection": settings.PROJECT_FILES_INDEX_NAME,
                    "file_metadata.milvus_file_index.embedding_model": settings.LLM_SERVICE_EMBEDDING_MODEL_NAME,
                    "file_metadata.milvus_file_index.chunk_count": chunk_count,
                },
            )
        except Exception as exc:
            logger.warning(
                f"File indexing failed for file {file_id}; "
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
