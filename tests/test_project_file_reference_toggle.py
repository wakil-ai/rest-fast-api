"""WK-267: the ``used_as_ai_reference`` flag must round-trip through listing and
be toggleable via the new PATCH endpoint. Mirrors
``test_project_files_response_shape.py``'s reload/patch/TestClient pattern.
"""

import importlib
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from models.chat_history import FileUploadResponse


def _stored_project_file(*, used_as_ai_reference: bool) -> dict:
    now = datetime.now(timezone.utc)
    return {
        "_id": "file-019e060f",
        "file_id": "file-019e060f",
        "user_id": "u1",
        "project_id": "p1",
        "scope": "project",
        "file_url": "/api/history/files/file-019e060f/view",
        "ocr_result": "contract text",
        "file_metadata": {
            "file_name": "contract.doc",
            "file_type": "application/msword",
            "file_size": 569344,
            "gcs_path": "projects/p1/file-019e060f/contract.doc",
        },
        "status": "completed",
        "used_as_ai_reference": used_as_ai_reference,
        "created_at": now,
        "updated_at": now,
    }


def _reference_response(*, used_as_ai_reference: bool) -> FileUploadResponse:
    record = _stored_project_file(used_as_ai_reference=used_as_ai_reference)
    return FileUploadResponse(
        _id=record["_id"],
        project_id=record["project_id"],
        file_url=record["file_url"],
        file_metadata=record["file_metadata"],
        ocr_result=record["ocr_result"],
        status=record["status"],
        used_as_ai_reference=used_as_ai_reference,
        created_at=record["created_at"],
        updated_at=record["updated_at"],
    )


@pytest.fixture
def projects_client():
    project_service_stub = MagicMock()
    project_service_stub.list_project_files = AsyncMock(
        return_value=[_stored_project_file(used_as_ai_reference=True)]
    )

    file_manager_stub = MagicMock()
    file_manager_stub.set_project_file_reference = AsyncMock(
        return_value=(200, _reference_response(used_as_ai_reference=False))
    )

    with (
        patch(
            "core.dependencies.get_project_service",
            return_value=project_service_stub,
        ),
        patch(
            "core.dependencies.get_file_manager", return_value=file_manager_stub
        ),
    ):
        import api.v2.history.projects as projects_module

        projects_module = importlib.reload(projects_module)

        app = FastAPI()
        app.include_router(projects_module.router)
        with TestClient(app) as client:
            yield client, file_manager_stub


def test_listing_round_trips_reference_flag(projects_client):
    client, _ = projects_client

    resp = client.get("/projects/p1/files", params={"user_id": "u1"})

    assert resp.status_code == 200
    assert resp.json()[0]["used_as_ai_reference"] is True


def test_default_is_false_for_records_predating_this_field():
    """Older Mongo docs have no ``used_as_ai_reference`` key at all — the
    model default must keep them out of AI context rather than opt them in."""
    resp = FileUploadResponse(
        _id="file-old",
        project_id="p1",
        file_url="/api/history/files/file-old/view",
        file_metadata={"file_name": "x", "file_type": "text/plain", "file_size": 1},
        ocr_result="",
        status="completed",
        created_at=datetime.now(timezone.utc),
        updated_at=datetime.now(timezone.utc),
    )
    assert resp.used_as_ai_reference is False


def test_patch_toggles_reference_and_returns_updated_value(projects_client):
    client, file_manager_stub = projects_client

    resp = client.patch(
        "/projects/p1/files/file-019e060f/reference",
        json={"user_id": "u1", "used_as_ai_reference": False},
    )

    assert resp.status_code == 200
    assert resp.json()["used_as_ai_reference"] is False
    file_manager_stub.set_project_file_reference.assert_awaited_once_with(
        project_id="p1",
        file_id="file-019e060f",
        user_id="u1",
        used_as_ai_reference=False,
    )


def test_patch_propagates_404_for_unknown_file(projects_client):
    client, file_manager_stub = projects_client
    file_manager_stub.set_project_file_reference.return_value = (404, "File not found")

    resp = client.patch(
        "/projects/p1/files/missing/reference",
        json={"user_id": "u1", "used_as_ai_reference": True},
    )

    assert resp.status_code == 404
