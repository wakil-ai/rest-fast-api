from __future__ import annotations

import asyncio
from typing import Any

from app.core.config import settings
from app.core.logger import logger
from app.db.db_manager import DBManager
from app.models.retrieval_models import RetrievalConfig, RetrievalResult
from app.retrieval.context_formatter import StandardContextFormatter
from app.retrieval.embedding_manager import EmbeddingManager


# Abstract base assistant
class BaseAssistant:
    """
    Abstract base for every assistant.
    """

    # Shared singletons — initialised once, reused everywhere.
    _db: DBManager | None = None
    _embedder: EmbeddingManager | None = None
    _formatter: StandardContextFormatter | None = None

    def __init__(self, collection_name: str, top_k: int = settings.TOP_K):
        self.collection_name = collection_name
        self.top_k = top_k

    @property
    def db(self) -> DBManager:
        if BaseAssistant._db is None:
            BaseAssistant._db = DBManager()
        return BaseAssistant._db

    @property
    def embedder(self) -> EmbeddingManager:
        if BaseAssistant._embedder is None:
            BaseAssistant._embedder = EmbeddingManager()
        return BaseAssistant._embedder

    @property
    def formatter(self) -> StandardContextFormatter:
        if BaseAssistant._formatter is None:
            BaseAssistant._formatter = StandardContextFormatter()
        return BaseAssistant._formatter

    async def retrieve(
        self,
        query: str,
        file_context: str = "",
        *,
        chat_history: str = "",
        **kwargs,
    ) -> RetrievalResult:
        """
        High-level retrieval: search → format.

        Override in subclasses only when the orchestration itself changes
        (e.g. multi-collection merging, intent classification).
        """
        try:
            effective_query = f"{query}\n\n\n{file_context}" if file_context else query
            config = self._build_config()

            raw_docs = await self.asearch(effective_query, config)
            result = await self.format_results(raw_docs)

            if file_context and result.context:
                result.context += f"\n\n\n{file_context}"

            return result
        except Exception as e:
            logger.error(
                f"[{self.__class__.__name__}] Retrieval failed: {e}", exc_info=True
            )
            return self._error_result()

    # Hooks for subclasses
    def search(self, query: str, config: RetrievalConfig) -> list[dict[str, Any]]:
        """Fetch raw documents (sync). Default: hybrid search."""
        embedding = self.embedder.embed_query(query)
        return self.db.search_hybrid(
            dense_vector=embedding,
            text_query=query,
            top_k=config.top_k,
            collection_name=config.collection_name,
            expr=config.filter or "",
        )

    async def asearch(self, query: str, config: RetrievalConfig) -> list[dict[str, Any]]:
        """Async wrapper — offloads embedding + vector search to a thread."""
        return await asyncio.to_thread(self.search, query, config)

    async def format_results(self, documents: list[dict[str, Any]]) -> RetrievalResult:
        """Format raw documents using the standard formatter."""
        return await self.formatter.format_results(documents)

    # Helpers
    def _build_config(self, **overrides) -> RetrievalConfig:
        defaults = dict(
            top_k=self.top_k,
            collection_name=self.collection_name,
        )
        defaults.update(overrides)
        return RetrievalConfig(**defaults)

    def _error_result(self) -> RetrievalResult:
        """Sensible fallback when retrieval fails."""
        if self.collection_name == settings.MILVUS_SHARTNOMA:
            return RetrievalResult(context="", attachments=[])
        return RetrievalResult(context="No relevant documents found.", attachments=[])
