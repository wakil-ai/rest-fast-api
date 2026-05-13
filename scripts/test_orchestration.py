#!/usr/bin/env python3
"""Simple smoke checks for orchestration helpers."""

from __future__ import annotations

from langchain_core.messages import HumanMessage
import sys
import os
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.models.retrieval_models import RetrievalResult
from app.orchestration import nodes
from app.orchestration.agents.web_search_fallback import merge_web_results, should_run_web_search
from app.orchestration.retrieval import (
    _updates_from_result,
    resolve_assistant,
    resolve_collection_name,
)
from app.orchestration.service import OrchestrationService


def main() -> None:
    assert resolve_collection_name("tax") == "tax"
    assert resolve_collection_name("customs") == "customs_legal_docs"

    assert (
        resolve_assistant(
            {
                "query": "q",
                "selected_assistant": "tax",
                "assistant_name": "main",
            }
        )
        == "tax"
    )

    ingest = nodes.ingest_payload({"query": "Penalty?"})
    assert isinstance(ingest["messages"][0], HumanMessage)
    assert "file_context" not in ingest

    updates = _updates_from_result(RetrievalResult(context="agent context"))
    assert updates["retrieval_context"] == "agent context"

    assert should_run_web_search(
        assistant_name="main",
        deep_research=True,
        evaluation={"is_sufficient": False},
    )
    assert not should_run_web_search(
        assistant_name="main",
        deep_research=False,
        evaluation={"is_sufficient": False},
    )

    merged = merge_web_results(
        "agent context",
        {
            "docs": [
                {
                    "url": "https://example.com",
                    "title": "Example",
                    "content": "Example content",
                }
            ]
        },
    )
    assert "agent context" in merged
    assert "https://example.com" in merged

    service = OrchestrationService()
    assert service.web_search_enabled({"deep_research": True})
    assert not service.web_search_enabled({"assistant_name": "tax"})

    print("orchestration smoke checks passed")


if __name__ == "__main__":
    main()
