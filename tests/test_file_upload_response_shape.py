"""Response shaping for file records.

rest-api-llm writes ingestion results into the shared ``files`` collection but
leaves ``status`` at its own value (``"pending"``), so the stored field is not a
trustworthy readiness signal. ``FileUploadResponse`` derives readiness from the
Milvus index instead, and keeps the index internals out of the payload.
"""

from datetime import datetime, timezone

from models.chat_history import FileUploadResponse


def _record(status: str, milvus_file_index: dict | None) -> dict:
    metadata: dict = {
        "file_name": "contract.doc",
        "file_type": "application/msword",
        "file_size": 569344,
        "gcs_path": "messages/112/file-abc/contract.doc",
        "file_content_hash": "48bfd0a68c1349e72ae6aebc3a08d9826b5a",
        "ocr_token_count": 62961,
    }
    if milvus_file_index is not None:
        metadata["milvus_file_index"] = milvus_file_index
    now = datetime.now(timezone.utc)
    return {
        "_id": "file-019e060f-e36e-7ec6-b4fb-59ec3ade8506",
        "file_url": "/api/history/files/file-019e060f/view",
        "file_metadata": metadata,
        "ocr_result": "А Й Б Л О В",
        "status": status,
        "created_at": now,
        "updated_at": now,
    }


INGESTED = {
    "chunk_count": 32,
    "collection": "files",
    "embedding_model": "Qwen/Qwen3-Embedding-4B",
    "enabled": True,
}


def test_ingested_file_reports_completed_regardless_of_stored_status():
    response = FileUploadResponse(**_record("pending", INGESTED))

    assert response.status == "completed"


def test_milvus_index_internals_are_not_exposed():
    response = FileUploadResponse(**_record("pending", INGESTED))

    assert "milvus_file_index" not in response.file_metadata
    assert response.file_metadata["file_name"] == "contract.doc"


def test_ocr_token_count_is_not_exposed():
    response = FileUploadResponse(**_record("pending", INGESTED))

    assert "ocr_token_count" not in response.file_metadata


def test_ocr_token_count_is_stripped_from_unindexed_files_too():
    response = FileUploadResponse(**_record("processing", None))

    assert "ocr_token_count" not in response.file_metadata


def test_unindexed_file_keeps_its_stored_status():
    response = FileUploadResponse(**_record("processing", {"enabled": False}))

    assert response.status == "processing"


def test_file_without_index_metadata_keeps_its_stored_status():
    response = FileUploadResponse(**_record("failed", None))

    assert response.status == "failed"


def test_source_record_is_not_mutated():
    record = _record("pending", INGESTED)

    FileUploadResponse(**record)

    assert record["status"] == "pending"
    assert record["file_metadata"]["milvus_file_index"] == INGESTED
    assert record["file_metadata"]["ocr_token_count"] == 62961
