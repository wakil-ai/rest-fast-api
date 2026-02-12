from __future__ import annotations

import os
from typing import Any

from app.assistants.base import BaseAssistant, RetrievalConfig, RetrievalResult
from app.chains.intent_classifier import IntentClassifier
from app.chains.milvus_agent import MilvusQueryAgent
from app.core.assistants import AssistantConfig
from app.core.config import settings
from app.core.logger import logger


class MamuriyAssistant(BaseAssistant):
    """Administrative-court assistant with file-level retrieval and domain routing.

    Owns its own:
    - Intent classification (tax vs general domain)
    - Milvus filter generation (court/instance/category)
    - File-level search expansion
    - Court-specific metadata formatting
    """

    # Number of unique files to return per search
    TOP_K_FILES = 3

    # Metadata fields rendered in formatted output
    METADATA_LABELS: dict[str, str] = {
        "responsible_judge_name": "Masul Sudya nomi",
        "speaker_judge_name": "Ma'ruzachi sudya",
        "hearing_date": "Sud majlisi sanasi",
        "result": "Sud qarori",
        "court_names_uz": "Sud nomi",
        "document_type_name_uz": "Hujjat turi",
        "categories_uz": "Sud toifalari",
        "instance": "Sud instantsiyasi",
    }

    def __init__(self):
        super().__init__(
            collection_name=settings.MILVUS_MAMURIY_SUD_ALL,
            top_k=settings.TOP_K,
        )
        self.intent_classifier = IntentClassifier()
        self.milvus_agent = MilvusQueryAgent()

    # Retrieve — classification + domain routing (all internal)
    async def retrieve(
        self,
        query: str,
        file_context: str = "",
        *,
        chat_history: str = "",
        **kwargs,
    ) -> RetrievalResult:
        """
        1. Classify intent  → domain_type + prompt template
        2. Generate Milvus filter expression
        3. Route to the correct multi-collection strategy
        """
        try:
            # Intent classification (returns domain str + PromptTemplate)
            domain_type, template = await self.intent_classifier.classify_intent(
                query, chat_history, file_context
            )
            logger.info(f"[MamuriyAssistant] Intent classified as domain: {domain_type}")

            # Milvus filter expression (court, instance, category)
            milvus_filter = await self.milvus_agent.generate_filter(
                query, chat_history, file_context
            )

            # Route by domain
            if domain_type == "tax":
                result = await self._retrieve_tax(query, file_context, milvus_filter)
            else:
                result = await self._retrieve_general(query, file_context, milvus_filter)

            result.prompt_template = template
            return result

        except Exception as e:
            logger.error(f"[MamuriyAssistant] Retrieval failed: {e}", exc_info=True)
            return self._error_result()

    # Search strategy (file-level)
    def search(self, query: str, config: RetrievalConfig) -> list[dict[str, Any]]:
        """
        Hybrid search followed by file-level expansion.

        For every unique ``file_id`` found in the top-K hits, fetch **all**
        chunks belonging to that file, sort by ``chunk_index``, and
        concatenate them into a single text.
        """
        embedding = self.embedder.embed_query(query)
        search_results = self.db.search_hybrid(
            dense_vector=embedding,
            text_query=query,
            top_k=config.top_k,
            collection_name=config.collection_name,
            expr=config.filter or "",
        )

        file_ids: set[str] = set()
        documents: list[dict[str, Any]] = []

        for doc in search_results:
            file_id = doc.get("metadata", {}).get("file_id")
            metadata = doc.get("metadata", {})

            if file_id in file_ids:
                continue
            file_ids.add(file_id)

            file_name = metadata.get("file_name", "")
            hierarchy = os.path.splitext(file_name)[0].replace("_", " ")

            # Fetch all chunks for this file
            file_filter = f"metadata['file_id'] == '{file_id}'"
            file_chunks = self.db.vector_handler.query(
                filter=file_filter,
                collection_name=config.collection_name,
            )

            file_chunks.sort(
                key=lambda x: self._safe_float(x["metadata"].get("chunk_index", 0))
            )
            file_text = "\n".join(c.get("text", "") for c in file_chunks)
            file_text = f"File Title: {hierarchy}\n\n{file_text}"

            documents.append(
                {
                    "text": file_text,
                    "metadata": {
                        "file_id": file_id,
                        "file_name": file_name,
                        "text": file_text,
                        "hierarchy": hierarchy,
                        **metadata,
                    },
                }
            )

            if len(documents) >= self.TOP_K_FILES:
                break

        return documents

    # Formatting (sud-specific metadata)
    async def format_results(
        self, documents: list[dict[str, Any]]
    ) -> RetrievalResult:
        """Format documents with administrative-court metadata fields."""
        entries: list[str] = []
        seen: set[str] = set()

        for doc in documents:
            metadata = doc.get("metadata", {})
            title = metadata.get("hierarchy", "—")

            meta_lines = [
                f"{label}: {metadata[key]}"
                for key, label in self.METADATA_LABELS.items()
                if metadata.get(key) not in (None, "")
            ]

            parts = ["-" * 50, f"Hierarchy: {title}"]
            if meta_lines:
                parts.append("\n")
                parts.extend(meta_lines)

            content = doc.get("text", "").strip()
            if content:
                parts.extend(["\n", "Hujjat matni:", content])

            entry = "\n".join(parts)
            if entry not in seen:
                seen.add(entry)
                entries.append(entry)

        if not entries:
            return RetrievalResult(context="Hech qanday hujjat topilmadi.", attachments=[])

        return RetrievalResult(
            context="\n\n".join(entries),
            attachments=[],
        )

    # Domain-specific retrieval (multi-collection merging)
    async def _retrieve_tax(
        self, query: str, file_context: str, filter: str
    ) -> RetrievalResult:
        """Merge results from mamuriy_sud + soliq collections."""
        half_k = max(1, self.top_k // 2)
        logger.info(f"TAX domain: {half_k} from mamuriy_sud + {half_k} from soliq")

        # mamuriy_sud portion
        mam_config = RetrievalConfig(
            top_k=half_k,
            collection_name=settings.MILVUS_MAMURIY_SUD_ALL,
            filter=filter,
        )
        effective = f"{query}\n\n\n{file_context}" if file_context else query
        mam_docs = self.search(effective, mam_config)
        mam_result = await self.format_results(mam_docs)

        # soliq portion (standard hybrid search + standard formatting)
        soliq_coll = AssistantConfig.get_collection_name("soliq")
        sol_embedding = self.embedder.embed_query(effective)
        sol_docs = self.db.search_hybrid(
            dense_vector=sol_embedding,
            text_query=effective,
            top_k=half_k,
            collection_name=soliq_coll,
        )
        sol_result = await self.formatter.format_results(sol_docs)

        combined_ctx = f"{mam_result.context}\n\n{'=' * 60}\n\n{sol_result.context}"
        if file_context:
            combined_ctx += f"\n\n\n{file_context}"

        return RetrievalResult(
            context=combined_ctx,
            attachments=mam_result.attachments + sol_result.attachments,
        )

    async def _retrieve_general(
        self, query: str, file_context: str, filter: str
    ) -> RetrievalResult:
        """Merge results from mamuriy_sud + main (lexuz) collections."""
        half_k = max(1, self.top_k // 2)
        logger.info(f"GENERAL domain: {half_k} from mamuriy_sud + {half_k} from main")

        effective = f"{query}\n\n\n{file_context}" if file_context else query

        # mamuriy_sud portion
        mam_config = RetrievalConfig(
            top_k=half_k,
            collection_name=settings.MILVUS_MAMURIY_SUD_ALL,
            filter=filter,
        )
        mam_docs = self.search(effective, mam_config)
        mam_result = await self.format_results(mam_docs)

        # main (lexuz) portion (standard hybrid search + standard formatting)
        main_embedding = self.embedder.embed_query(effective)
        main_docs = self.db.search_hybrid(
            dense_vector=main_embedding,
            text_query=effective,
            top_k=half_k,
            collection_name=settings.MILVUS_MAIN_NAME,
        )
        main_result = await self.formatter.format_results(main_docs)

        combined_ctx = f"{mam_result.context}\n\n{'=' * 60}\n\n{main_result.context}"
        if file_context:
            combined_ctx += f"\n\n\n{file_context}"

        return RetrievalResult(
            context=combined_ctx,
            attachments=mam_result.attachments + main_result.attachments,
        )

    # Helpers
    @staticmethod
    def _safe_float(val) -> float:
        try:
            return float(val)
        except (ValueError, TypeError):
            return 0.0
