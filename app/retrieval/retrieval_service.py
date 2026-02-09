from typing import Any, Optional

from app.core.config import settings
from app.core.logger import logger
from app.retrieval.context_formatter import DocumentDeduplicator, DocumentFormatter
from app.retrieval.search_strategies import RetrievalConfig, SearchStrategy


class RetrievalService:
    """
    Main retrieval service orchestrating all retrieval operations.

    Provides unified interface for:
    - Standard document retrieval
    - Contract document retrieval
    - Project-scoped retrieval
    - Multilingual retrieval
    """

    def __init__(self):
        self.search_strategy = SearchStrategy()
        self.formatter = DocumentFormatter()
        self.deduplicator = DocumentDeduplicator()

    async def retrieve_context(
        self,
        query: str,
        top_k: int = settings.TOP_K,
        alpha: float = settings.ALPHA,
        search_type: str = "hybrid",
        collection_name: str = settings.MILVUS_MAIN_NAME,
        file_context: Optional[str] = None,
    ) -> tuple[str, list[dict[str, Any]]]:
        """
        Retrieve and format top documents.

        Args:
            query: Search query
            top_k: Number of documents to retrieve
            alpha: Hybrid search weighting parameter
            search_type: Type of search to perform
            collection_name: Target collection
            file_context: Optional additional context for retrieval

        Returns:
            Tuple of (formatted_context, attachments)
        """
        try:
            config = RetrievalConfig(
                top_k=top_k,
                alpha=alpha,
                search_type=search_type,
                collection_name=collection_name,
            )

            # # Retrieve documents
            # if file_context:
            #     search_results = await self._retrieve_with_file_context(
            #         query, file_context, config
            #     )
            # else:
            # Experimental: always use both query and file context if available
            if file_context:
                query = f"{query}\n\n\n{file_context}"
            search_results = await self._retrieve_raw_documents(query, config)

            # Format results based on collection type
            formatted_result = await self.formatter.format_results(
                search_results, collection_name
            )

            if isinstance(formatted_result, dict):
                return (
                    formatted_result["formatted_text"],
                    formatted_result["attachments"],
                )
            else:
                return (formatted_result, [])

        except Exception as e:
            logger.error(f"Retrieval failed: {e}", exc_info=True)
            return self._get_error_response(collection_name)

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

        Args:
            query_translations: Dict mapping language codes to translated queries
            top_k: Number of final documents to return
            alpha: Hybrid search parameter
            search_type: Type of search
            collection_name: Target collection

        Returns:
            Deduplicated and sorted list of documents
        """
        all_results = []
        per_lang_top_k = int(top_k * 1.5)  # Retrieve more per language

        config = RetrievalConfig(
            top_k=per_lang_top_k,
            alpha=alpha,
            search_type=search_type,
            collection_name=collection_name,
        )

        for lang_code, query in query_translations.items():
            try:
                logger.info(f"Retrieving for {lang_code}: {query}")
                results = await self._retrieve_raw_documents(query, config)
                all_results.extend(results)
            except Exception as e:
                logger.error(f"Search failed for {lang_code}: {e}")

        if not all_results:
            return []

        # Deduplicate and sort
        unique_results = self.deduplicator.deduplicate_by_url(all_results)
        sorted_results = sorted(
            unique_results, key=lambda x: x.get("score", 0), reverse=True
        )

        return sorted_results[:top_k]

    async def retrieve_project_context(
        self,
        query: str,
        project_id: str,
        user_id: str,
        top_k: int = settings.TOP_K,
        alpha: float = settings.ALPHA,
    ) -> str:
        """
        Retrieve documents from a specific project.

        Args:
            query: Search query
            project_id: Project identifier
            user_id: User identifier
            top_k: Number of documents
            alpha: Hybrid search parameter

        Returns:
            Formatted context string
        """
        try:
            expr = f'metadata["project_id"] == "{project_id}" and metadata["user_id"] == "{user_id}"'

            config = RetrievalConfig(
                top_k=top_k,
                alpha=alpha,
                search_type="hybrid",
                collection_name=settings.MILVUS_PROJECT_FILES,
            )

            search_results = await self._retrieve_raw_documents(
                query, config, expr=expr
            )

            return await self.formatter.format_standard_results(search_results)

        except Exception as e:
            logger.error(f"Project retrieval failed: {e}", exc_info=True)
            return "No relevant documents found in the project files."

    async def _retrieve_raw_documents(
        self, query: str, config: RetrievalConfig, expr: Optional[str] = None
    ) -> list[dict[str, Any]]:
        """Retrieve raw documents without formatting."""
        return self.search_strategy.search(query, config, expr)

    async def _retrieve_with_file_context(
        self, query: str, file_context: str, config: RetrievalConfig
    ) -> list[dict[str, Any]]:
        """Retrieve documents using both query and file context."""
        half_k = config.top_k // 2

        # Create configs for both searches
        query_config = RetrievalConfig(
            top_k=half_k,
            alpha=config.alpha,
            search_type=config.search_type,
            collection_name=config.collection_name,
        )

        # Search with both query and file context
        query_results = await self._retrieve_raw_documents(query, query_config)
        file_results = await self._retrieve_raw_documents(file_context, query_config)

        # Combine and deduplicate
        combined = query_results + file_results
        unique = self.deduplicator.deduplicate_by_url(combined)

        # Sort by score and take top_k
        return sorted(unique, key=lambda x: x.get("score", 0), reverse=True)[
            : config.top_k
        ]

    @staticmethod
    def _get_error_response(collection_name: str) -> tuple[str, list]:
        """Get appropriate error response based on collection."""
        if collection_name == settings.MILVUS_SHARTNOMA:
            return ("", [])
        return ("No relevant documents found.", [])
