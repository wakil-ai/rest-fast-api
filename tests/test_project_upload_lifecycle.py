import asyncio
from contextlib import suppress
from copy import deepcopy
from datetime import datetime, timezone
from io import BytesIO
from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest
from fastapi import UploadFile

from services import file_management as module
from services.file_management import FileManager


@pytest.fixture
def upload_setup(monkeypatch, tmp_path):
    manager = FileManager.__new__(FileManager)
    manager._project_processing_tasks = {}
    manager.storage = MagicMock()
    manager.history = MagicMock()
    now = datetime.now(timezone.utc)
    record = {
        '_id': 'file-1', 'status': 'processing', 'project_id': 'project-1',
        'scope': 'project', 'file_metadata': {}, 'created_at': now, 'updated_at': now,
    }
    manager.history.get_files_by_project = AsyncMock(return_value=[])
    manager.history.get_file_by_content_hash_for_project = AsyncMock(return_value=None)
    manager.history.add_file_upload = AsyncMock(return_value=deepcopy(record))
    manager.history.get_file_by_id = AsyncMock(side_effect=lambda _: deepcopy(record))

    async def update(_, fields):
        record.update(fields)
        return deepcopy(record)

    async def update_job(_, task_id, fields):
        applied = (record.get('processing_task_id') == task_id
                   and record.get('processing_status') not in {'succeeded', 'failed'})
        if applied:
            record.update(fields)
        return deepcopy(record), applied

    manager.history.update_file_metadata_fields = AsyncMock(side_effect=update)
    manager.history.update_file_processing_fields = AsyncMock(side_effect=update_job)
    project = MagicMock()
    project.get_project = AsyncMock()
    project.append_file_id = AsyncMock()
    project.increment_stat = AsyncMock()
    monkeypatch.setattr('core.dependencies.get_project_service', lambda: project)
    monkeypatch.setattr(module, 'send_project_file_progress_webhook', AsyncMock())
    client = MagicMock()
    client.submit_document_processing = AsyncMock(return_value={'task_id': 'job-1', 'status': 'queued'})
    client.get_document_processing_status = AsyncMock(return_value={'status': 'started'})
    monkeypatch.setattr(module, 'get_llm_service_client', lambda: client)
    temp = tmp_path / 'upload.txt'
    temp.write_text('document')
    manager._spool_upload_to_temp = AsyncMock(return_value=(str(temp), 'hash', 8))
    manager._build_file_id = lambda: 'file-1'
    manager.storage.upload_file_from_path.return_value = 'gs://test/file-1'
    return manager, client, record, project, temp


async def upload(manager):
    return await manager.upload_project_file(
        UploadFile(filename='document.txt', file=BytesIO(b'document')),
        user_id='user-1', project_id='project-1',
    )


async def stop_tasks(manager):
    for task in list(manager._project_processing_tasks.values()):
        task.cancel()
        with suppress(asyncio.CancelledError):
            await task


@pytest.mark.asyncio
async def test_upload_ack_requires_persisted_processing_job(upload_setup):
    manager, client, record, _, temp = upload_setup
    async def wait_for_ocr(_):
        await asyncio.Event().wait()

    manager._await_processing_success = AsyncMock(side_effect=wait_for_ocr)
    status, response = await upload(manager)
    try:
        assert status == 200
        assert response.processing_task_id == record.get('processing_task_id') == 'job-1'
        client.submit_document_processing.assert_awaited_once()
        assert not temp.exists()
    finally:
        await stop_tasks(manager)


@pytest.mark.asyncio
async def test_processing_submission_failure_is_not_acknowledged(upload_setup):
    manager, client, record, _, temp = upload_setup
    request = httpx.Request('POST', 'http://test/process')
    client.submit_document_processing.side_effect = httpx.HTTPStatusError(
        'unavailable', request=request, response=httpx.Response(503, request=request),
    )
    status, _ = await upload(manager)
    assert status == 503
    assert record['status'] == 'failed'
    assert not temp.exists()
    assert not manager._project_processing_tasks


@pytest.mark.asyncio
async def test_cancelled_poller_can_resume_same_job_without_resubmission(upload_setup):
    manager, client, record, project, _ = upload_setup
    status, _ = await upload(manager)
    await stop_tasks(manager)
    assert status == 200
    assert record.get('processing_task_id') == 'job-1'
    # A fresh API worker receives a file-list request after the original exited.
    restarted = FileManager.__new__(FileManager)
    restarted.history = manager.history
    restarted._project_processing_tasks = {}
    client.get_document_processing_status.return_value = {
        'status': 'succeeded', 'result': {'chunk_count': 2, 'collection': 'files'},
    }
    restarted.resume_project_file_processing(deepcopy(record))
    await asyncio.gather(*restarted._project_processing_tasks.values())
    assert record['status'] == 'completed'
    client.submit_document_processing.assert_awaited_once()
    project.increment_stat.assert_awaited_once_with('project-1', 'docs', 1)


@pytest.mark.asyncio
async def test_repeated_upload_reuses_active_job(upload_setup):
    manager, client, record, _, _ = upload_setup
    record.update(processing_task_id='existing-job', processing_status='started')
    manager.history.get_file_by_content_hash_for_project.return_value = deepcopy(record)
    status, response = await upload(manager)
    try:
        assert status == 200
        assert response.processing_task_id == 'existing-job'
        await asyncio.sleep(0)
        client.submit_document_processing.assert_not_awaited()
    finally:
        await stop_tasks(manager)


@pytest.mark.asyncio
async def test_competing_pollers_only_finish_and_count_job_once(upload_setup):
    manager, client, record, project, _ = upload_setup
    record.update(processing_task_id='job-1', processing_status='started')
    client.get_document_processing_status.return_value = {
        'status': 'succeeded', 'result': {'chunk_count': 2},
    }
    await asyncio.gather(*(manager._poll_processing_job(deepcopy(record)) for _ in range(2)))
    assert record['status'] == 'completed'
    project.increment_stat.assert_awaited_once_with('project-1', 'docs', 1)


@pytest.mark.asyncio
async def test_old_failed_poller_does_not_overwrite_new_attempt(upload_setup):
    manager, client, record, _, _ = upload_setup
    old = {**record, 'processing_task_id': 'old-job', 'processing_status': 'started'}
    record.update(processing_task_id='new-job', processing_status='queued')
    client.get_document_processing_status.return_value = {'status': 'failed', 'error': 'old failure'}
    await manager._poll_processing_job(old)
    assert record['status'] == 'processing'
    assert record['processing_task_id'] == 'new-job'
    assert record['processing_status'] == 'queued'


@pytest.mark.asyncio
async def test_history_updates_only_current_nonterminal_job():
    from services.chat_history_service import ChatHistoryService

    history = ChatHistoryService.__new__(ChatHistoryService)
    history.files_collection = 'files'
    history.db_manager = MagicMock()
    history.db_manager.update_documents = AsyncMock(return_value=0)
    history.get_file_by_id = AsyncMock(return_value={'processing_task_id': 'new-job'})
    current, changed = await history.update_file_processing_fields(
        'file-1', 'old-job', {'status': 'failed'},
    )
    assert not changed
    assert current['processing_task_id'] == 'new-job'
    query = history.db_manager.update_documents.call_args.args[1]
    assert query == {
        '_id': 'file-1', 'processing_task_id': 'old-job',
        'processing_status': {'$nin': ['succeeded', 'failed']},
    }
