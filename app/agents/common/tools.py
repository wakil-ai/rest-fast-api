"""Tool helpers for chat-agent graphs."""
from __future__ import annotations

from typing import Any

from app.core.config import settings
from app.core.logger import logger
from langchain_community.tools.tavily_search import TavilySearchResults


def build_web_search_tool() -> Any | None:
    """Return a Tavily web-search tool when TAVILY_API_KEY is configured."""
    if not settings.TAVILY_API_KEY:
        logger.warning("TAVILY_API_KEY is not configured; skipping web search tool")
        return None
    return TavilySearchResults(api_key=settings.TAVILY_API_KEY, max_results=5)

__all__ = ["build_web_search_tool"]
