import os
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
    filter: str = ""


class SearchStrategy:
    """Handles different search strategies."""

    def __init__(self):
        self.db_manager = DBManager()
        self.embedding_manager = EmbeddingManager()

    def search(
        self, query: str, config: RetrievalConfig, expr: Optional[str] = None
    ) -> list[dict[str, Any]]:
        """Execute search based on configuration."""
        # Update config with expr if provided (for backward compatibility)
        if expr:
            config.filter = expr

        search_methods = {
            "sparse": self._search_sparse,
            "dense": self._search_dense,
            "specific": self._search_specific,
            "hybrid": self._search_hybrid,
        }

        # Special case for soliq assistant
        if config.collection_name == settings.MILVUS_SOLIQ_ASSISTANT_NAME:
            return self._search_soliq_assistant(query, config)

        if (
            config.collection_name == settings.MILVUS_MAMURIY_SUD
            or config.collection_name == settings.MILVUS_MAMURIY_SUD_ALL
        ):
            return self._search_mamuriy_sud(query, config)

        search_method = search_methods.get(config.search_type, self._search_hybrid)
        return search_method(query, config)

    def _search_sparse(
        self, query: str, config: RetrievalConfig
    ) -> list[dict[str, Any]]:
        """Perform BM25 keyword-based search."""
        return self.db_manager.search_sparse(
            text_query=query, top_k=config.top_k, collection_name=config.collection_name
        )

    def _search_dense(
        self, query: str, config: RetrievalConfig
    ) -> list[dict[str, Any]]:
        """Perform semantic embedding-based search."""
        embedding = self.embedding_manager.embed_query(query)
        return self.db_manager.search_dense(
            embedding, config.top_k, config.collection_name
        )

    def _search_hybrid(
        self, query: str, config: RetrievalConfig
    ) -> list[dict[str, Any]]:
        """Perform hybrid search combining dense and sparse."""
        embedding = self.embedding_manager.embed_query(query)
        return self.db_manager.search_hybrid(
            dense_vector=embedding,
            text_query=query,
            top_k=config.top_k,
            alpha=config.alpha,
            collection_name=config.collection_name,
            expr=config.filter or "",
        )

    def _search_specific(
        self, query: str, config: RetrievalConfig
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

    def _search_mamuriy_sud(
        self,
        query: str,
        config: RetrievalConfig,
        top_k_file: int = 3,
    ) -> list[dict[str, Any]]:
        """Perform search for mamuriy sud with expression filtering."""
        embedding = self.embedding_manager.embed_query(query)
        search_results = self.db_manager.search_hybrid(
            dense_vector=embedding,
            text_query=query,
            top_k=config.top_k,
            collection_name=config.collection_name,
            expr=config.filter or "",
        )

        file_ids = set()
        documents = []
        for doc in search_results:
            file_id = doc.get("metadata", {}).get("file_id")
            metadata = doc.get("metadata", {})

            if file_id in file_ids:
                continue  # Skip duplicate file_ids
            file_ids.add(file_id)

            file_name = doc.get("metadata", {}).get("file_name", "")
            hierarchy = os.path.splitext(file_name)[0].replace("_", " ")

            filter = f"metadata['file_id'] == '{file_id}'"
            searched_file_results = self.db_manager.vector_handler.query(
                filter=filter,
                collection_name=config.collection_name,
            )

            # Sort files with chunk_id in the metadata
            def parse_chunk_index(val):
                try:
                    return float(val)
                except (ValueError, TypeError):
                    return 0.0

            searched_file_results.sort(
                key=lambda x: parse_chunk_index(x["metadata"].get("chunk_index", 0))
            )
            file_texts = file_texts = "\n".join(
                file_doc.get("text", "") for file_doc in searched_file_results
            )
            file_texts = f"File Title: {hierarchy}\n\n{file_texts}"
            documents.append(
                {
                    "text": file_texts,
                    "metadata": {
                        "file_id": file_id,
                        "file_name": file_name,
                        "text": file_texts,
                        "hierarchy": hierarchy,
                        **metadata,
                    },
                }
            )

            if len(documents) >= top_k_file:
                break

        return documents
