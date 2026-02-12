from __future__ import annotations

from typing import Any, Optional

from app.assistants.base import BaseAssistant, RetrievalConfig
from app.core.config import settings
from app.core.logger import logger
from app.db.db_manager import DBManager
from app.retrieval.embedding_manager import EmbeddingManager
from app.retrieval.context_formatter import StandardContextFormatter

class RetrievalService:
    """
    Thin retrieval layer for generic (non-assistant-specific) operations.

    Assistant-specific retrieval is handled by each assistant's ``retrieve()``
    method.  This service is used by:
    - ``/api/retrieval`` endpoints (direct vector search)
    - ``AgenticRAGFlow`` (multilingual retrieval + project context)
    """

    def __init__(self):
        self._db = DBManager()
        self._embedder = EmbeddingManager()
        self._formatter = StandardContextFormatter()

    # Generic retrieval (API endpoints)
    async def retrieve_context(
        self,
        query: str,
        top_k: int = settings.TOP_K,
        alpha: float = settings.ALPHA,
        search_type: str = "hybrid",
        collection_name: str = settings.MILVUS_MAIN_NAME,
        file_context: Optional[str] = None,
        filter: str = "",
    ) -> tuple[str, list[dict[str, Any]]]:
        """
        Generic retrieval + standard formatting.

        This is called from the ``/api/retrieval`` endpoints where there is
        no assistant context.  Delegates to the base-class standard formatter.
        """
        try:
            config = RetrievalConfig(
                top_k=top_k,
                alpha=alpha,
                search_type=search_type,
                collection_name=collection_name,
                filter=filter,
            )

            effective_query = f"{query}\n\n\n{file_context}" if file_context else query
            raw_docs = self._search(effective_query, config)

            result = await self._formatter.format_results(raw_docs)
            return result.context, result.attachments

        except Exception as e:
            logger.error(f"Retrieval failed: {e}", exc_info=True)
            return self._error_response(collection_name)

    # Multilingual retrieval (deep research / agentic RAG)
    async def retrieve_multilingual(
        self,
        query_translations: dict[str, str],
        top_k: int = settings.TOP_K,
        alpha: float = settings.ALPHA,
        search_type: str = "hybrid",
        collection_name: str = settings.MILVUS_MAIN_NAME,
    ) -> list[dict[str, Any]]:
        """
        Retrieve and merge documents for multiple query translations.

        Returns deduplicated and score-sorted raw documents.
        """
        all_results: list[dict[str, Any]] = []
        per_lang_top_k = int(top_k * 1.5)

        config = RetrievalConfig(
            top_k=per_lang_top_k,
            alpha=alpha,
            search_type=search_type,
            collection_name=collection_name,
        )

        for lang_code, query in query_translations.items():
            try:
                logger.info(f"Retrieving for {lang_code}: {query}")
                results = self._search(query, config)
                all_results.extend(results)
            except Exception as e:
                logger.error(f"Search failed for {lang_code}: {e}")

        if not all_results:
            return []

        unique = BaseAssistant.deduplicate_by_url(all_results)
        unique.sort(key=lambda x: x.get("score", 0), reverse=True)
        return unique[:top_k]
    
    
    # Low-level search dispatcher
    def _search(self, query: str, config: RetrievalConfig) -> list[dict[str, Any]]:
        """Route to the correct search method based on ``config.search_type``."""
        embedding = self._embedder.embed_query(query)

        if config.search_type == "sparse":
            return self._db.search_sparse(
                text_query=query,
                top_k=config.top_k,
                collection_name=config.collection_name,
            )

        if config.search_type == "dense":
            return self._db.search_dense(
                embedding, config.top_k, config.collection_name
            )

        if config.search_type == "specific":
            return self._db.search_specific(
                text_query=query,
                top_k=config.top_k,
                collection_name=config.collection_name,
            )

        # Default: hybrid
        return self._db.search_hybrid(
            dense_vector=embedding,
            text_query=query,
            top_k=config.top_k,
            alpha=config.alpha,
            collection_name=config.collection_name,
            expr=config.filter or "",
        )

    # Helpers
    @staticmethod
    def _error_response(collection_name: str) -> tuple[str, list]:
        if collection_name == settings.MILVUS_SHARTNOMA:
            return ("", [])
        return ("No relevant documents found.", [])
