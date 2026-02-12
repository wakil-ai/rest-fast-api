from typing import Any, Optional

from app.core.assistants import AssistantConfig
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

    # Standard retrieval methods
    async def _retrieve_standard(
        self, query: str, collection_name: str, file_context: str
    ) -> tuple[str, list[dict[str, Any]]]:
        ctx, att = await self.retrieve_context(
            query=query,
            top_k=settings.TOP_K,
            collection_name=collection_name,
            file_context=file_context or None,
        )
        if file_context:
            ctx += f"\n\n\n{file_context}"
        return ctx, att

    # Domain-specific retrieval methods > Administrative Court for TAX
    async def _retrieve_tax_domain(
        self, query: str, file_context: str, filter: str = ""
    ) -> tuple[str, list]:
        """Retrieve for TAX domain: half from mamuriy_sud + half from soliq"""
        half_k = max(1, settings.TOP_K // 2)
        logger.info(
            f"TAX domain: retrieving {half_k} from mamuriy_sud + {half_k} from soliq"
        )

        # Retrieve from mamuriy_sud collection
        mam_ctx, mam_att = await self.retrieve_context(
            query=query,
            top_k=half_k,
            collection_name=settings.MILVUS_MAMURIY_SUD_ALL,  # experimental: use ALL collection for tax too
            file_context=file_context or None,
            filter=filter,
        )

        # Retrieve from soliq collection
        soliq_coll = AssistantConfig.get_collection_name("soliq")
        sol_ctx, sol_att = await self.retrieve_context(
            query=query,
            top_k=half_k,
            collection_name=soliq_coll,
            file_context=file_context or None,
        )

        combined = f"{mam_ctx}\n\n{'='*60}\n\n{sol_ctx}"
        if file_context:
            combined += f"\n\n\n{file_context}"

        return combined, mam_att + sol_att

    # Domain-specific retrieval methods > Administrative Court for GENERAL
    async def _retrieve_general_domain(
        self, query: str, file_context: str, filter: str = ""
    ) -> tuple[str, list]:
        """Retrieve for GENERAL domain: half from mamuriy_sud + half from main (lexuz) database"""
        half_k = max(1, settings.TOP_K // 2)
        logger.info(
            f"GENERAL domain: retrieving {half_k} from mamuriy_sud + {half_k} from main database"
        )

        # Retrieve from mamuriy_sud collection
        mam_ctx, mam_att = await self.retrieve_context(
            query=query,
            top_k=half_k,
            collection_name=settings.MILVUS_MAMURIY_SUD_ALL,
            file_context=file_context or None,
            filter=filter,
        )

        # Retrieve from main (lexuz) collection - general legal database
        main_coll = settings.MILVUS_MAIN_NAME
        main_ctx, main_att = await self.retrieve_context(
            query=query,
            top_k=half_k,
            collection_name=main_coll,
            file_context=file_context or None,
        )

        combined = f"{mam_ctx}\n\n{'='*60}\n\n{main_ctx}"
        if file_context:
            combined += f"\n\n\n{file_context}"

        return combined, mam_att + main_att

    # Standard retrieval interface
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
                filter=filter,
            )

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

    # Multilingual retrieval interface > used for Deep Research
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

    # User project retrieval
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

    # Low-level retrieval
    async def _retrieve_raw_documents(
        self, query: str, config: RetrievalConfig, expr: Optional[str] = None
    ) -> list[dict[str, Any]]:
        """Retrieve raw documents without formatting."""
        return self.search_strategy.search(query, config, expr)

    @staticmethod
    def _get_error_response(collection_name: str) -> tuple[str, list]:
        """Get appropriate error response based on collection."""
        if collection_name == settings.MILVUS_SHARTNOMA:
            return ("", [])
        return ("No relevant documents found.", [])
