"""Tests for collect_user_uploaded_context's size-tiered file handling.

Covers the fix for a file's visibility silently downgrading mid-conversation:
previously, any indexed file always went through a top-K vector search keyed
to the current query, even when its full OCR text (still on the record)
would trivially answer a differently-phrased follow-up the search missed.

A file within FILE_CONTENT_TOKEN_LIMIT now goes in whole regardless of index
status; only a file that exceeds the budget falls back to vector search (if
indexed) or a bounded, truncated excerpt (if not yet indexed).
"""

from unittest.mock import AsyncMock

import pytest

import services.chat_service as chat_service_module
from core.config import settings
from services.chat_service import ChatService


def _make_service(file_record: dict):
    service = ChatService.__new__(ChatService)
    service.chat_history_service = AsyncMock()
    service.chat_history_service.get_file_by_id.return_value = file_record
    return service


@pytest.fixture(autouse=True)
def _deterministic_tokens(monkeypatch):
    """1 char = 1 token, and set a small, easy-to-reason-about budget."""
    monkeypatch.setattr(chat_service_module, "count_tokens", len)
    monkeypatch.setattr(
        chat_service_module, "truncate_to_token_limit", lambda text, limit: text[:limit]
    )
    monkeypatch.setattr(settings, "FILE_CONTENT_TOKEN_LIMIT", 10)


@pytest.mark.asyncio
async def test_small_file_goes_in_whole_when_not_indexed():
    service = _make_service({
        "file_metadata": {"file_name": "petition.pdf", "milvus_file_index": {}},
        "ocr_result": "short text",  # 10 chars == the budget, still within it
    })

    context = await service.collect_user_uploaded_context(
        query="what does it say", user_id="u1", file_ids=["f1"]
    )

    assert "short text" in context
    assert "USER FILE CONTEXT: petition.pdf" in context
    assert "truncated" not in context


@pytest.mark.asyncio
async def test_small_file_goes_in_whole_even_once_indexed(monkeypatch):
    """The bug: an indexed file used to always lose full-text access, even
    when it easily fit the budget. Retrieval must not even be attempted —
    it would be a strictly lossy subset of text already included whole."""
    service = _make_service({
        "file_metadata": {"file_name": "petition.pdf", "milvus_file_index": {"enabled": True}},
        "ocr_result": "short text",
    })
    search_files = AsyncMock()
    monkeypatch.setattr(
        chat_service_module, "get_llm_service_client", lambda: AsyncMock(search_files=search_files)
    )

    context = await service.collect_user_uploaded_context(
        query="what does it say", user_id="u1", file_ids=["f1"]
    )

    assert "short text" in context
    search_files.assert_not_awaited()


@pytest.mark.asyncio
async def test_large_indexed_file_falls_back_to_vector_search(monkeypatch):
    service = _make_service({
        "file_metadata": {"file_name": "contract.pdf", "milvus_file_index": {"enabled": True}},
        "ocr_result": "this text is way over the ten character budget",
    })
    search_files = AsyncMock(return_value={"context": "## USER FILE CONTEXT (uploaded files)\n[chunk]"})
    monkeypatch.setattr(
        chat_service_module, "get_llm_service_client", lambda: AsyncMock(search_files=search_files)
    )

    context = await service.collect_user_uploaded_context(
        query="what does it say", user_id="u1", file_ids=["f1"]
    )

    search_files.assert_awaited_once()
    call_kwargs = search_files.await_args.args[0]
    assert call_kwargs["file_ids"] == ["f1"]
    assert call_kwargs["top_k"] == settings.FILE_SEARCH_TOP_K
    assert "[chunk]" in context
    assert "this text is way over" not in context


@pytest.mark.asyncio
async def test_large_unindexed_file_gets_a_bounded_excerpt_not_nothing():
    """Indexing has not finished yet: no vector search is possible, but the
    model should get a truncated excerpt rather than silently nothing."""
    service = _make_service({
        "file_metadata": {"file_name": "contract.pdf", "milvus_file_index": {}},
        "ocr_result": "this text is way over the ten character budget",
    })

    context = await service.collect_user_uploaded_context(
        query="what does it say", user_id="u1", file_ids=["f1"]
    )

    assert "truncated, indexing in progress" in context
    assert "this text " in context  # the 10-char-truncated prefix
    assert "budget" not in context  # confirms it was actually cut, not included whole


@pytest.mark.asyncio
async def test_missing_file_record_is_skipped_without_error():
    service = _make_service(None)

    context = await service.collect_user_uploaded_context(
        query="q", user_id="u1", file_ids=["missing"]
    )

    assert context == ""
