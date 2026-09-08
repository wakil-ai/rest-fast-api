import asyncio
import hashlib
import os
import tempfile
from datetime import datetime, timezone

from fastapi import UploadFile
import httpx
from uuid6 import uuid7

from core.config import settings
from core.dependencies import (
    get_chat_history_service,
    get_llm_service_client,
    get_storage_service,
)
from core.logger import logger
from models.chat_history import FileUploadResponse
from utils.progress_webhook import send_project_file_progress_webhook
from utils.upload_limits import max_file_count, max_file_size_bytes, max_file_size_mb


class DocumentProcessingSubmissionError(Exception):
    def __init__(self, message: str, status_code: int = 500) -> None:
        super().__init__(message)
        self.status_code = status_code


class FileManager:
    """Handles file upload, OCR processing, storage and metadata persistence."""

    PROCESSING_POLL_TIMEOUT_SECONDS = settings.DOCUMENT_PROCESSING_POLL_TIMEOUT_SECONDS

    def __init__(self):
        self.storage = get_storage_service()
        self.history = get_chat_history_service()

    async def upload_message_file(
        self, file: UploadFile, user_id: str
    ) -> tuple[int, FileUploadResponse | str]:
        """
        Process file upload for a message: validate → store → wait for processing.
        Returns: (status_code, response_or_error_message)
        """
        temp_path, content_hash, file_size = await self._spool_upload_to_temp(
            file, file.filename or "upload"
        )

        if file_size > max_file_size_bytes("message"):
            self._safe_remove_temp_file(temp_path)
            return (
                413,
                f"File exceeds the {max_file_size_mb('message')} MB limit for chat uploads.",
            )

        try:
            existing_by_content = await self.history.get_file_by_content_hash(
                content_hash=content_hash,
                user_id=user_id,
            )
            if existing_by_content:
                existing_file_id = existing_by_content["_id"]
                if self._is_ingested_to_milvus(existing_by_content):
                    logger.info(
                        f"Reusing existing ingested file by content hash for "
                        f"file_id: {existing_file_id}"
                    )
                    response = self._build_success_response(
                        file_id=existing_file_id,
                        metadata=existing_by_content.get("file_metadata", {}),
                        ocr_result=existing_by_content.get("ocr_result", ""),
                        record=existing_by_content,
                        project_id=None,
                    )
                    return 200, response
                logger.info(
                    f"Existing file cache hit is not ingested; reprocessing "
                    f"file_id: {existing_file_id}"
                )
                existing_by_content = await self._submit_processing_job(
                    file_id=existing_file_id,
                    temp_path=temp_path,
                    filename=file.filename or "upload",
                    content_type=file.content_type or "application/octet-stream",
                    user_id=user_id,
                    record=existing_by_content,
                )
                existing_by_content = await self._await_processing_success(
                    existing_by_content
                )
                response = self._build_success_response(
                    file_id=existing_file_id,
                    metadata=existing_by_content.get("file_metadata", {}),
                    ocr_result=existing_by_content.get("ocr_result", ""),
                    record=existing_by_content,
                    project_id=None,
                )
                return 200, response

            file_id = self._build_file_id()

            existing_record = await self.history.get_file_by_id(file_id)
            if existing_record:
                logger.info(
                    f"Reusing existing file record for deterministic file_id: {file_id}"
                )
                response = self._build_success_response(
                    file_id=file_id,
                    metadata=existing_record.get("file_metadata", {}),
                    ocr_result=existing_record.get("ocr_result", ""),
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
                ocr_result="",
                file_metadata=metadata,
                status="processing",
                scope="message",
            )

            record = await self._submit_processing_job(
                file_id=file_id,
                temp_path=temp_path,
                filename=file.filename or "upload",
                content_type=file.content_type or "application/octet-stream",
                user_id=user_id,
                record=record,
            )

            record = await self._await_processing_success(record)

            response = self._build_success_response(
                file_id=file_id,
                metadata=record.get("file_metadata", metadata),
                ocr_result=record.get("ocr_result", ""),
                record=record,
                project_id=None,
            )

            logger.info(
                f"File uploaded successfully: {file.filename}",
            )
            return 200, response

        except Exception as e:
            status_code = (
                e.status_code
                if isinstance(e, DocumentProcessingSubmissionError)
                else 500
            )
            logger.error(
                f"Message file upload failed: {file.filename} - {str(e)}",
                exc_info=True,
            )
            return status_code, str(e)

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
        Upload a project document and hand it to rest-api-llm for end-to-end processing.
        """
        from core.dependencies import get_project_service

        project_service = get_project_service()
        await project_service.get_project(project_id, user_id)

        project_file_cap = max_file_count("project")
        existing_project_files = await self.history.get_files_by_project(
            project_id, limit=project_file_cap
        )
        if len(existing_project_files) >= project_file_cap:
            return 413, f"This project already has the maximum of {project_file_cap} files."

        temp_path, content_hash, file_size = await self._spool_upload_to_temp(
            file, file.filename or "upload"
        )

        if file_size > max_file_size_bytes("project"):
            self._safe_remove_temp_file(temp_path)
            return (
                413,
                f"File exceeds the {max_file_size_mb('project')} MB limit for project uploads.",
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
                if self._is_ingested_to_milvus(existing_by_content):
                    logger.info(
                        f"Reusing existing ingested project file by content hash for "
                        f"file_id: {fid}"
                    )
                    response = self._build_success_response(
                        file_id=fid,
                        metadata=existing_by_content.get("file_metadata", {}),
                        ocr_result=existing_by_content.get("ocr_result", ""),
                        record=existing_by_content,
                        project_id=project_id,
                    )
                    return 200, response
                logger.info(
                    f"Existing project file cache hit is not ingested; reprocessing "
                    f"file_id: {fid}"
                )
                existing_by_content = await self._submit_processing_job(
                    file_id=fid,
                    temp_path=temp_path,
                    filename=file.filename or "upload",
                    content_type=file.content_type or "application/octet-stream",
                    user_id=user_id,
                    record=existing_by_content,
                    project_id=project_id,
                    session_id=session_id,
                )
                existing_by_content = await self._await_processing_success(
                    existing_by_content
                )
                response = self._build_success_response(
                    file_id=fid,
                    metadata=existing_by_content.get("file_metadata", {}),
                    ocr_result=existing_by_content.get("ocr_result", ""),
                    record=existing_by_content,
                    project_id=project_id,
                )
                return 200, response

            file_id = self._build_file_id()
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
                ocr_result="",
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
                stage="processing_queued",
                progress_percent=10,
                status="processing",
            )

            record = await self._submit_processing_job(
                file_id=file_id,
                temp_path=temp_path,
                filename=file.filename or "upload",
                content_type=file.content_type or "application/octet-stream",
                user_id=user_id,
                record=record,
                project_id=project_id,
                session_id=session_id,
            )

            record = await self._await_processing_success(record)

            response = self._build_success_response(
                file_id=file_id,
                metadata=record.get("file_metadata", metadata),
                ocr_result=record.get("ocr_result", ""),
                record=record,
                project_id=project_id,
            )
            logger.info(
                f"Project file upload accepted: {file.filename} project={project_id}"
            )
            return 200, response

        except Exception as e:
            status_code = (
                e.status_code
                if isinstance(e, DocumentProcessingSubmissionError)
                else 500
            )
            logger.error(
                f"Project file upload failed: {file.filename} - {str(e)}",
                exc_info=True,
            )
            return status_code, str(e)

        finally:
            self._safe_remove_temp_file(temp_path)

    async def delete_project_file(
        self,
        project_id: str,
        file_id: str,
        user_id: str,
    ) -> tuple[int, str | None]:
        """Remove a project-scoped file from Mongo/index metadata and the project record."""
        from core.dependencies import get_project_service

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

    async def set_project_file_reference(
        self,
        project_id: str,
        file_id: str,
        user_id: str,
        used_as_ai_reference: bool,
    ) -> tuple[int, FileUploadResponse | str]:
        """Toggle whether a project file is included as AI context (WK-267)."""
        from core.dependencies import get_project_service

        project_service = get_project_service()
        await project_service.get_project(project_id, user_id)

        file_record = await self.history.get_file_by_id(file_id)
        if not file_record:
            return 404, "File not found"
        if file_record.get("project_id") != project_id:
            return 404, "File not found"
        if file_record.get("scope") != "project":
            return 400, "File is not scoped to a project"

        updated = await self.history.update_file_metadata_fields(
            file_id, {"used_as_ai_reference": used_as_ai_reference}
        )
        response = self._build_success_response(
            file_id=file_id,
            metadata=updated.get("file_metadata", {}),
            ocr_result=updated.get("ocr_result", ""),
            record=updated,
            project_id=project_id,
        )
        return 200, response

    async def _submit_processing_job(
        self,
        *,
        file_id: str,
        temp_path: str,
        filename: str,
        content_type: str,
        user_id: str,
        record: dict,
        project_id: str | None = None,
        session_id: str | None = None,
        message_id: str | None = None,
    ) -> dict:
        try:
            with open(temp_path, "rb") as file_obj:
                result = await get_llm_service_client().submit_document_processing(
                    document_id=file_id,
                    file=file_obj,
                    filename=filename,
                    content_type=content_type,
                    user_id=user_id,
                    project_id=project_id,
                    session_id=session_id,
                    message_id=message_id,
                    force_index=True,
                )
        except httpx.HTTPStatusError as exc:
            status_code = exc.response.status_code
            if status_code == 413:
                message = "File is too large for document processing."
            elif status_code == 422:
                message = "Document processing request is invalid."
            else:
                message = "Document processing could not be started."
            await self._mark_processing_failed(
                record,
                message,
                processing_status="failed",
            )
            raise DocumentProcessingSubmissionError(message, status_code) from exc
        except Exception as exc:
            message = "Document processing could not be started."
            await self._mark_processing_failed(
                record,
                message,
                processing_status="failed",
            )
            raise DocumentProcessingSubmissionError(message) from exc

        task_id = str(result.get("task_id") or "").strip()
        if not task_id:
            message = "Document processing did not return a task id."
            await self._mark_processing_failed(
                record,
                message,
                processing_status="failed",
            )
            raise DocumentProcessingSubmissionError(message)

        return await self.history.update_file_metadata_fields(
            file_id,
            {
                "processing_task_id": task_id,
                "processing_status": str(result.get("status") or "queued"),
                "processing_error": None,
                "processing_submitted_at": datetime.now(timezone.utc),
            },
        )

    async def _await_processing_success(self, record: dict) -> dict:
        if self._processing_completed_successfully(record):
            return record
        if not self._processing_is_active(record):
            raise DocumentProcessingSubmissionError(
                str(
                    record.get("processing_error")
                    or "Document processing did not complete successfully."
                )
            )

        final_record = await self._poll_processing_job(record)
        if final_record and self._processing_completed_successfully(final_record):
            return final_record
        raise DocumentProcessingSubmissionError(
            str(
                (final_record or {}).get("processing_error")
                or "Document processing did not complete successfully."
            )
        )

    async def _poll_processing_job(self, record: dict) -> dict | None:
        from core.dependencies import get_project_service

        file_id = str(record.get("_id") or record.get("file_id") or "")
        task_id = str(record.get("processing_task_id") or "")
        project_id = record.get("project_id")
        webhook_url = str(record.get("webhook_url") or "")
        started_at = asyncio.get_running_loop().time()
        last_status: str | None = None

        if not file_id or not task_id:
            logger.warning(
                f"[FileManager] Cannot poll processing job without file/task id: {record}"
            )
            return None

        while True:
            elapsed = asyncio.get_running_loop().time() - started_at
            if elapsed > self.PROCESSING_POLL_TIMEOUT_SECONDS:
                return await self._mark_processing_failed(
                    record,
                    "Document processing timed out.",
                    processing_status="failed",
                )

            try:
                payload = await get_llm_service_client().get_document_processing_status(
                    document_id=file_id,
                    task_id=task_id,
                )
            except httpx.HTTPStatusError as exc:
                if exc.response.status_code == 404:
                    return await self._mark_processing_failed(
                        record,
                        "Document processing job expired.",
                        processing_status="failed",
                    )
                logger.warning(
                    f"[FileManager] Polling failed for file_id={file_id}: {exc}",
                    exc_info=True,
                )
                await asyncio.sleep(self._processing_poll_interval(elapsed))
                continue
            except Exception as exc:
                logger.warning(
                    f"[FileManager] Polling failed for file_id={file_id}: {exc}",
                    exc_info=True,
                )
                await asyncio.sleep(self._processing_poll_interval(elapsed))
                continue

            status = str(payload.get("status") or "").strip().lower()
            if status in {"queued", "started"}:
                await self.history.update_file_metadata_fields(
                    file_id,
                    {
                        "processing_status": status,
                    },
                )
                if project_id and status != last_status:
                    await send_project_file_progress_webhook(
                        webhook_url,
                        project_id=project_id,
                        file_id=file_id,
                        stage=f"processing_{status}",
                        progress_percent=25 if status == "started" else 10,
                        status="processing",
                    )
                last_status = status
                await asyncio.sleep(self._processing_poll_interval(elapsed))
                continue

            if status == "failed":
                return await self._mark_processing_failed(
                    record,
                    str(payload.get("error") or "Document processing failed."),
                    processing_status="failed",
                )

            if status == "succeeded":
                result = payload.get("result") or {}
                chunk_count = int(result.get("chunk_count") or 0)
                collection = (
                    result.get("collection") or settings.PROJECT_FILES_INDEX_NAME
                )
                skipped = bool(result.get("skipped"))
                skip_reason = result.get("reason")
                if skipped:
                    return await self._mark_processing_failed(
                        record,
                        f"Document processing skipped Milvus ingestion: {skip_reason or 'unknown'}",
                        processing_status="failed",
                    )
                final_record = await self.history.update_file_metadata_fields(
                    file_id,
                    {
                        "status": "completed",
                        "processing_status": "succeeded",
                        "processing_error": None,
                        "processed_chunk_count": chunk_count,
                        "processing_skipped": skipped,
                        "processing_skip_reason": skip_reason,
                        "processing_completed_at": datetime.now(timezone.utc),
                        "file_metadata.milvus_file_index.enabled": not skipped,
                        "file_metadata.milvus_file_index.collection": collection,
                        "file_metadata.milvus_file_index.chunk_count": chunk_count,
                        "file_metadata.milvus_file_index.reason": skip_reason,
                    },
                )
                if project_id:
                    await send_project_file_progress_webhook(
                        webhook_url,
                        project_id=project_id,
                        file_id=file_id,
                        stage="ingestion_complete"
                        if not skipped
                        else "ingestion_skipped",
                        progress_percent=100,
                        status="completed",
                        extra={"chunk_count": chunk_count, "reason": skip_reason},
                    )
                    await get_project_service().increment_stat(project_id, "docs", 1)
                return final_record

            logger.warning(
                f"[FileManager] Unknown document processing status for "
                f"file_id={file_id}: {status!r}"
            )
            await asyncio.sleep(self._processing_poll_interval(elapsed))

    async def _mark_processing_failed(
        self,
        record: dict,
        error: str,
        *,
        processing_status: str,
    ) -> dict | None:
        file_id = str(record.get("_id") or record.get("file_id") or "")
        if not file_id:
            return None
        failed_record = await self.history.update_file_metadata_fields(
            file_id,
            {
                "status": "failed",
                "processing_status": processing_status,
                "processing_error": error,
                "processing_completed_at": datetime.now(timezone.utc),
                "file_metadata.milvus_file_index.enabled": False,
                "file_metadata.milvus_file_index.error": error,
            },
        )
        project_id = record.get("project_id")
        if project_id:
            await send_project_file_progress_webhook(
                str(record.get("webhook_url") or ""),
                project_id=project_id,
                file_id=file_id,
                stage="failed",
                progress_percent=100,
                status="failed",
                extra={"error": error},
            )
        return failed_record

    @staticmethod
    def _processing_poll_interval(elapsed_seconds: float) -> float:
        if elapsed_seconds < 30:
            return 2
        if elapsed_seconds < 5 * 60:
            return 5
        return 15

    @staticmethod
    def _processing_is_active(record: dict) -> bool:
        return bool(record.get("processing_task_id")) and record.get(
            "processing_status"
        ) != "failed"

    @staticmethod
    def _processing_completed_successfully(record: dict) -> bool:
        return (
            record.get("status") == "completed"
            and record.get("processing_status") == "succeeded"
            and FileManager._is_ingested_to_milvus(record)
        )

    @staticmethod
    def _is_ingested_to_milvus(record: dict) -> bool:
        metadata = record.get("file_metadata") or {}
        milvus_file_index = metadata.get("milvus_file_index") or {}
        return bool(milvus_file_index.get("enabled"))

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
            processing_task_id=record.get("processing_task_id"),
            processing_status=record.get("processing_status"),
            processing_error=record.get("processing_error"),
            processed_chunk_count=record.get("processed_chunk_count"),
            processing_skipped=record.get("processing_skipped"),
            processing_skip_reason=record.get("processing_skip_reason"),
            used_as_ai_reference=record.get("used_as_ai_reference", False),
            created_at=record["created_at"],
            updated_at=record["updated_at"],
        )

    @staticmethod
    def _build_file_id() -> str:
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
