"""Integration tests for the upload entitlement gate at the API layer.

Boots the real ``files`` router inside a minimal FastAPI app and drives it with
``TestClient`` to prove that, for a free user, the request is rejected with the
402 contract **before** ``FileManager`` is ever touched.

The router constructs its service singletons at import time (one of which needs a
running event loop), so the dependency getters are patched *before* the module is
imported/reloaded — the module then binds our mocks.
"""

import importlib
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from models.chat_history import FileUploadResponse
from utils.entitlements import FILE_UPLOAD_REQUIRES_PAID_PLAN


def _valid_upload_response() -> FileUploadResponse:
    now = datetime.now(timezone.utc)
    return FileUploadResponse(
        _id="file-test-123",
        project_id=None,
        file_url="/api/history/files/file-test-123/view",
        file_metadata={"file_name": "doc.txt", "file_type": "text/plain", "file_size": 3},
        ocr_result="abc",
        status="completed",
        created_at=now,
        updated_at=now,
    )


def _build_client(*, can_upload: bool):
    """Yield (TestClient, upload_mock) with the entitlement decision pinned."""
    upload_mock = AsyncMock(return_value=(200, _valid_upload_response()))
    file_manager_mock = MagicMock()
    file_manager_mock.upload_message_file = upload_mock

    rate_limit_stub = MagicMock()
    rate_limit_stub.can_upload_files = AsyncMock(return_value=can_upload)

    with (
        patch(
            "core.dependencies.get_file_manager",
            return_value=file_manager_mock,
        ),
        patch(
            "core.dependencies.get_chat_history_service",
            return_value=MagicMock(),
        ),
        patch(
            "core.dependencies.get_storage_service",
            return_value=MagicMock(),
        ),
    ):
        import api.v2.history.files as files_module

        files_module = importlib.reload(files_module)

        app = FastAPI()
        app.include_router(files_module.router)

        # Keep the entitlement decision patched for the duration of the request.
        with patch(
            "utils.entitlements.get_rate_limit_service",
            return_value=rate_limit_stub,
        ):
            with TestClient(app) as client:
                yield client, upload_mock


@pytest.fixture
def free_user_client():
    yield from _build_client(can_upload=False)


@pytest.fixture
def paid_user_client():
    yield from _build_client(can_upload=True)


def test_free_user_blocked_before_file_manager(free_user_client):
    client, upload_mock = free_user_client

    resp = client.post(
        "/files",
        data={"user_id": "free-user"},
        files={"file": ("doc.txt", b"abc", "text/plain")},
    )

    assert resp.status_code == 402
    detail = resp.json()["detail"]
    assert detail["code"] == FILE_UPLOAD_REQUIRES_PAID_PLAN
    assert detail["upgrade_required"] is True

    # The whole point: file processing never started.
    upload_mock.assert_not_called()


def test_paid_user_reaches_file_manager(paid_user_client):
    client, upload_mock = paid_user_client

    resp = client.post(
        "/files",
        data={"user_id": "paid-user"},
        files={"file": ("doc.txt", b"abc", "text/plain")},
    )

    assert resp.status_code == 200
    upload_mock.assert_awaited_once()
    # user_id is forwarded to the file manager.
    assert upload_mock.await_args.args[1] == "paid-user"
