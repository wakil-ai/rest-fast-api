"""WK-267: project chat context must only include files flagged
``used_as_ai_reference`` — the old whole-project blind search (search_project)
had no file-level filter at all, so it's replaced by folding flagged file ids
into the existing per-file OCR/vector-search path.
"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from services.chat_service import ChatService


def _project_file(file_id: str, *, used_as_ai_reference: bool, ocr_result: str) -> dict:
    return {
        "_id": file_id,
        "project_id": "proj-1",
        "scope": "project",
        "used_as_ai_reference": used_as_ai_reference,
        "file_metadata": {"file_name": f"{file_id}.txt"},
        "ocr_result": ocr_result,
    }


def _build_service(project_files: list[dict]) -> ChatService:
    service = ChatService.__new__(ChatService)
    service.chat_history_service = MagicMock()
    service.chat_history_service.get_files_by_project = AsyncMock(
        return_value=project_files
    )
    service.chat_history_service.get_file_by_id = AsyncMock(
        side_effect=lambda fid: next(
            (f for f in project_files if f["_id"] == fid), None
        )
    )
    return service


@pytest.mark.asyncio
async def test_only_flagged_project_files_reach_context():
    files = [
        _project_file("file-flagged", used_as_ai_reference=True, ocr_result="flagged body"),
        _project_file("file-unflagged", used_as_ai_reference=False, ocr_result="unflagged body"),
    ]
    service = _build_service(files)

    with (
        patch("services.chat_service.get_project_service") as get_project_service,
        patch("services.chat_service.get_llm_service_client") as get_llm_client,
    ):
        get_project_service.return_value.get_project_instructions = AsyncMock(
            return_value={"instructions": ""}
        )
        llm_client = MagicMock()
        get_llm_client.return_value = llm_client

        context = await service.collect_user_uploaded_context(
            query="what does the contract say",
            user_id="user-1",
            file_ids=None,
            project_id="proj-1",
        )

    assert "flagged body" in context
    assert "unflagged body" not in context
    llm_client.search_project.assert_not_called()


@pytest.mark.asyncio
async def test_explicit_file_ids_merge_with_flagged_project_files():
    files = [
        _project_file("file-flagged", used_as_ai_reference=True, ocr_result="flagged body"),
    ]
    service = _build_service(files)
    # An explicit file_id from the request, not itself a project file.
    service.chat_history_service.get_file_by_id = AsyncMock(
        side_effect=lambda fid: {
            "file-flagged": files[0],
            "file-explicit": {
                "_id": "file-explicit",
                "file_metadata": {"file_name": "explicit.txt"},
                "ocr_result": "explicit body",
            },
        }.get(fid)
    )

    with (
        patch("services.chat_service.get_project_service") as get_project_service,
        patch("services.chat_service.get_llm_service_client") as get_llm_client,
    ):
        get_project_service.return_value.get_project_instructions = AsyncMock(
            return_value={"instructions": ""}
        )
        get_llm_client.return_value = MagicMock()

        context = await service.collect_user_uploaded_context(
            query="q",
            user_id="user-1",
            file_ids=["file-explicit"],
            project_id="proj-1",
        )

    assert "flagged body" in context
    assert "explicit body" in context
