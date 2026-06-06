from __future__ import annotations

from src.orchestration.utils import (
    context_evaluation_llm,
    web_search_tavily,
    WebSearchResponse,
)
from src.core.assistants import AssistantConfig


async def evaluate_context_sufficiency(query: str, retrieval_context: str) -> dict:
    evaluation = await context_evaluation_llm(
        query,
        retrieval_context or "No context retrieved.",
    )
    return evaluation.model_dump()


def should_run_web_search(
    *,
    assistant_name: str | None,
    deep_research: bool,
    evaluation: dict | None,
) -> bool:
    if not (deep_research or AssistantConfig.is_web_search_enabled(assistant_name)):
        return False
    if not evaluation:
        return False
    return evaluation.get("is_sufficient") is False


async def run_web_search_fallback(query: str) -> dict:
    response = await web_search_tavily(query)
    return response.model_dump()


def merge_web_results(
    retrieval_context: str,
    web_response: dict | WebSearchResponse,
) -> str:
    if isinstance(web_response, WebSearchResponse):
        payload = web_response
    else:
        payload = WebSearchResponse.model_validate(web_response)

    combined = (retrieval_context or "").rstrip()
    combined += "\n\n--- Web Search Results ---\n"
    for doc in payload.docs:
        combined += f"\nSource: {doc.url}\n"
        if doc.title:
            combined += f"Title: {doc.title}\n"
        combined += f"{doc.content}\n"
    return combined
