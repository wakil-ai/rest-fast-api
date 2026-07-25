"""``GET /projects/{id}/files`` must not leak Milvus index internals.

The route returned raw Mongo documents, so it bypassed the shaping that
``FileUploadResponse`` applies everywhere else. Boots the real projects router
with a stubbed project service, mirroring ``test_upload_entitlement_integration``.
"""

import importlib
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient


def _stored_project_file() -> dict:
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
            "file_content_hash": "48bfd0a68c",
            "milvus_file_index": {
                "chunk_count": 32,
                "collection": "files",
                "embedding_model": "Qwen/Qwen3-Embedding-4B",
                "enabled": True,
            },
        },
        # rest-api-llm finished ingestion but never advanced `status`.
        "status": "pending",
        "created_at": now,
        "updated_at": now,
    }


@pytest.fixture
def projects_client():
    project_service_stub = MagicMock()
    project_service_stub.list_project_files = AsyncMock(
        return_value=[_stored_project_file()]
    )

    with (
        patch(
            "core.dependencies.get_project_service",
            return_value=project_service_stub,
        ),
        patch("core.dependencies.get_file_manager", return_value=MagicMock()),
    ):
        import api.v2.history.projects as projects_module

        projects_module = importlib.reload(projects_module)

        app = FastAPI()
        app.include_router(projects_module.router)
        with TestClient(app) as client:
            yield client


def test_listing_hides_milvus_index_internals(projects_client):
    resp = projects_client.get("/projects/p1/files", params={"user_id": "u1"})

    assert resp.status_code == 200
    assert "milvus_file_index" not in resp.json()[0]["file_metadata"]


def test_listing_reports_ingested_file_as_completed(projects_client):
    resp = projects_client.get("/projects/p1/files", params={"user_id": "u1"})

    assert resp.json()[0]["status"] == "completed"
