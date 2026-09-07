"""Unit tests for WK-267 per-scope upload size/count limits.

Exercises the real ``FileManager.upload_message_file`` / ``upload_project_file``
(only their storage/history/project-service dependencies are mocked) to prove the
size and count guards return a 413 tuple *before* any GCS upload or Mongo write —
mirroring the "before FileManager is ever touched" style of
``test_upload_entitlement_integration.py``, one layer down.
"""

import io
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import UploadFile

from core.config import settings


def _build_file_manager(*, existing_project_files: list[dict] | None = None):
    with (
        patch("core.dependencies.get_storage_service", return_value=MagicMock()),
        patch(
            "core.dependencies.get_chat_history_service",
            return_value=MagicMock(),
        ),
    ):
        from services.file_management import FileManager

        manager = FileManager()

    manager.storage.upload_file_from_path = MagicMock()
    manager.history.add_file_upload = AsyncMock()
    manager.history.get_file_by_content_hash = AsyncMock(return_value=None)
    manager.history.get_file_by_content_hash_for_project = AsyncMock(return_value=None)
    manager.history.get_files_by_project = AsyncMock(
        return_value=existing_project_files or []
    )
    return manager


def _upload_file(name: str, data: bytes) -> UploadFile:
    return UploadFile(filename=name, file=io.BytesIO(data))


@pytest.mark.asyncio
async def test_message_upload_over_size_limit_returns_413():
    manager = _build_file_manager()
    oversized = (settings.MAIN_CHAT_MAX_FILE_SIZE_MB + 1) * 1024 * 1024

    status, message = await manager.upload_message_file(
        _upload_file("big.pdf", b"x" * oversized), "user-1"
    )

    assert status == 413
    assert str(settings.MAIN_CHAT_MAX_FILE_SIZE_MB) in message
    manager.storage.upload_file_from_path.assert_not_called()
    manager.history.add_file_upload.assert_not_awaited()


@pytest.mark.asyncio
async def test_project_upload_over_size_limit_returns_413():
    manager = _build_file_manager()
    oversized = (settings.PROJECT_MAX_FILE_SIZE_MB + 1) * 1024 * 1024

    with patch("core.dependencies.get_project_service") as get_project_service:
        project_service = MagicMock()
        project_service.get_project = AsyncMock(return_value={"_id": "proj-1"})
        project_service.append_file_id = AsyncMock()
        get_project_service.return_value = project_service

        status, message = await manager.upload_project_file(
            _upload_file("big.pdf", b"x" * oversized),
            user_id="user-1",
            project_id="proj-1",
        )

    assert status == 413
    assert str(settings.PROJECT_MAX_FILE_SIZE_MB) in message
    manager.storage.upload_file_from_path.assert_not_called()
    manager.history.add_file_upload.assert_not_awaited()


@pytest.mark.asyncio
async def test_project_upload_at_file_count_cap_returns_413():
    existing = [{"_id": f"file-{i}"} for i in range(settings.PROJECT_MAX_FILES)]
    manager = _build_file_manager(existing_project_files=existing)

    with patch("core.dependencies.get_project_service") as get_project_service:
        project_service = MagicMock()
        project_service.get_project = AsyncMock(return_value={"_id": "proj-1"})
        get_project_service.return_value = project_service

        status, message = await manager.upload_project_file(
            _upload_file("one-more.txt", b"hello"),
            user_id="user-1",
            project_id="proj-1",
        )

    assert status == 413
    assert str(settings.PROJECT_MAX_FILES) in message
    # The count cap must reject before ever spooling/hashing the upload.
    manager.storage.upload_file_from_path.assert_not_called()
    manager.history.add_file_upload.assert_not_awaited()
