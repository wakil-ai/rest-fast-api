from typing import Any

from app.agents.base import BaseAgent, RetrievalConfig
from app.core.config import settings


class TaxAgent(BaseAgent):
    """Tax assistant with dense-only vector search."""

    def __init__(self):
        super().__init__(
            collection_name=settings.MILVUS_TAX_COLLECTION,
            assistant_name="tax",
        )

    def search(self, query: str, config: RetrievalConfig) -> list[dict[str, Any]]:
        """Dense-only search optimized for the tax Q&A collection."""
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
