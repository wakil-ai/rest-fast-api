from __future__ import annotations

from typing import Any

from langchain_core.tools import tool

from app.agents.base import BaseAgent, RetrievalConfig
from app.agents.common.state import AgentRequestContext, AgentState
from app.core.config import settings
from app.core.logger import logger


class TaxAgent(BaseAgent):
    """Tax assistant with dense-only vector search across lex.uz and buxgalter.uz sources."""

    def __init__(self):
        super().__init__(
            collection_name=settings.MILVUS_TAX_COLLECTION,
            assistant_name="tax",
        )

    def _context_guidance_text(self) -> str:
        return (
            "Use `search_tax_corpus` to retrieve tax regulations from soliq.uz, lex.uz, and "
            "buxgalter.uz sources. "
            "Use `get_chat_history` for conversation context and `search_memory` for user preferences. "
            "Uploaded file content (when present) is provided directly below; only call "
            "`get_uploaded_file_context` for refined keyword searches across the same files. "
            "Cite only from retrieved sources; do not invent tax rules or rates."
        )

    def _build_domain_tools(
        self, request: AgentRequestContext, state: AgentState
    ) -> list[Any]:
        @tool
        async def search_tax_corpus(query: str) -> str:
            """Search Uzbekistan tax law from soliq.uz, lex.uz, and buxgalter.uz sources."""
            try:
                result = await self.retrieve(query=query, file_context="", chat_history="")
                return result.context or "No relevant tax documents found."
            except Exception as exc:
                logger.warning(f"search_tax_corpus failed: {exc}", exc_info=True)
                return "Tax search failed; answer from general reasoning where appropriate."

        return [search_tax_corpus]

    def search(self, query: str, config: RetrievalConfig) -> list[dict[str, Any]]:
        """Three-pass search prioritising lex.uz then buxgalter.uz then all sources."""
        embedding = self.embedder.embed_query(query)

        filters = [
            'metadata["url"] like "%lex.uz%"',
            'metadata["url"] like "%buxgalter.uz%"',
            None,
        ]

        search_results = []
        for expr in filters:
            top_k_half = config.top_k // 2
            if expr is None:
                top_k_half = config.top_k

            results = self.db.search_hybrid(
                dense_vector=embedding,
                text_query=query,
                top_k=top_k_half,
                collection_name=config.collection_name,
                expr=expr,
            )
            search_results.extend(results)

        return search_results
