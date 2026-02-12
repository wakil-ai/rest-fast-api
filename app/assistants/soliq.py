from typing import Any

from app.assistants.base import BaseAssistant, RetrievalConfig
from app.core.config import settings


class SoliqAssistant(BaseAssistant):
    """Tax-law assistant with dense-only vector search.

    Overrides only search (dense-only); inherits standard formatting from base.
    """

    def __init__(self):
        super().__init__(collection_name=settings.MILVUS_SOLIQ_ASSISTANT_NAME)

    def search(self, query: str, config: RetrievalConfig) -> list[dict[str, Any]]:
        """Dense-only search optimised for the soliq Q&A collection."""
        embedding = self.embedder.embed_query(query)

        filters = [
            'metadata["url"] like "%lex.uz%"',
            'metadata["url"] like "%buxgalter.uz%"',
            None,
        ]

        search_results = []
        for expr in filters:
            # make top-k on half of the top_k
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
