from dataclasses import dataclass
from typing import Any, Optional

from app.core.config import settings
from app.db.db_manager import DBManager
from app.retrieval.embedding_manager import EmbeddingManager


@dataclass
class RetrievalConfig:
    """Configuration for retrieval operations."""

    top_k: int = settings.TOP_K
    alpha: float = settings.ALPHA
    search_type: str = "hybrid"
    collection_name: str = settings.MILVUS_MAIN_NAME


class SearchStrategy:
    """Handles different search strategies."""

    def __init__(self):
        self.db_manager = DBManager()
        self.embedding_manager = EmbeddingManager()

    def search(
        self, query: str, config: RetrievalConfig, expr: Optional[str] = None
    ) -> list[dict[str, Any]]:
        """Execute search based on configuration."""
        search_methods = {
            "sparse": self._search_sparse,
            "dense": self._search_dense,
            "specific": self._search_specific,
            "hybrid": self._search_hybrid,
        }

        # Special case for soliq assistant
        if config.collection_name == settings.MILVUS_SOLIQ_ASSISTANT_NAME:
            return self._search_soliq_assistant(query, config)

        search_method = search_methods.get(config.search_type, self._search_hybrid)
        return search_method(query, config, expr)

    def _search_sparse(
        self, query: str, config: RetrievalConfig, expr: Optional[str] = None
    ) -> list[dict[str, Any]]:
        """Perform BM25 keyword-based search."""
        return self.db_manager.search_sparse(
            text_query=query, top_k=config.top_k, collection_name=config.collection_name
        )

    def _search_dense(
        self, query: str, config: RetrievalConfig, expr: Optional[str] = None
    ) -> list[dict[str, Any]]:
        """Perform semantic embedding-based search."""
        embedding = self.embedding_manager.embed_query(query)
        return self.db_manager.search_dense(
            embedding, config.top_k, config.collection_name
        )

    def _search_hybrid(
        self, query: str, config: RetrievalConfig, expr: Optional[str] = None
    ) -> list[dict[str, Any]]:
        """Perform hybrid search combining dense and sparse."""
        embedding = self.embedding_manager.embed_query(query)
        return self.db_manager.search_hybrid(
            dense_vector=embedding,
            text_query=query,
            top_k=config.top_k,
            alpha=config.alpha,
            collection_name=config.collection_name,
            expr=expr or "",
        )

    def _search_specific(
        self, query: str, config: RetrievalConfig, expr: Optional[str] = None
    ) -> list[dict[str, Any]]:
        """Perform specific search for article numbers."""
        return self.db_manager.search_specific(
            text_query=query, top_k=config.top_k, collection_name=config.collection_name
        )

    def _search_soliq_assistant(
        self, query: str, config: RetrievalConfig
    ) -> list[dict[str, Any]]:
        """Perform search for soliq assistant."""
        embedding = self.embedding_manager.embed_query(query)
        return self.db_manager.vector_handler.query_soliq_assistant(
            dense_vector=embedding,
            top_k=config.top_k,
            collection_name=config.collection_name,
        )
