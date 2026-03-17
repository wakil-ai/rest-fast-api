from __future__ import annotations

import asyncio
import os
from typing import Any

from app.assistants.base import BaseAssistant, RetrievalConfig, RetrievalResult
from app.core.config import settings
from app.core.dependencies import get_milvus_query_agent, get_prompt_registry
from app.core.logger import logger
from app.utils.text_cleaning import clean_pdf_html_text


class EconomicCourtAssistant(BaseAssistant):
    """Economic-court assistant.

    Uses the default hybrid retrieval from BaseAssistant, but with a
    Economic-procedure focused system prompt .
    """

    # Number of unique files to return per search
    TOP_K_FILES = 3  # NOT THE TOP_K

    # Metadata fields rendered in formatted output
    METADATA_LABELS: dict[str, str] = {
        "case_number": "Ish raqami",
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
            collection_name=settings.MILVUS_ECONOMIC_COURT,
            top_k=settings.TOP_K,
        )
        self.milvus_agent = get_milvus_query_agent()
        self.prompt_registry = get_prompt_registry()

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
        1. Generate Milvus filter expression
        2. Route to the correct multi-collection strategy
        """
        try:
            template = self.prompt_registry.get_assistant_prompt("economic_court")

            # Milvus filter expression (court, instance, category)
            milvus_filter = await self.milvus_agent.generate_filter(
                query,
                chat_history,
                file_context,
                assistant="economic_court",
            )

            result = await self._retrieve_general(query, file_context, milvus_filter)

            result.prompt_template = template
            return result

        except Exception as e:
            logger.error(
                f"[EconomicCourtAssistant] Retrieval failed: {e}", exc_info=True
            )
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
            file_text = clean_pdf_html_text(file_text) # Cleaning HTML artifacts from PDF extraction (common in court documents)
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
    async def format_results(self, documents: list[dict[str, Any]]) -> RetrievalResult:
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
            return RetrievalResult(
                context="Hech qanday hujjat topilmadi.", attachments=[]
            )

        return RetrievalResult(
            context="\n\n".join(entries),
            attachments=[],
        )

    async def _retrieve_general(
        self, query: str, file_context: str, filter: str
    ) -> RetrievalResult:
        """Merge results from economic court + main (lexuz) collections."""
        effective = f"{query}\n\n\n{file_context}" if file_context else query

        # Administrative-court portion
        eco_config = RetrievalConfig(
            top_k=settings.TOP_K,
            collection_name=settings.MILVUS_ECONOMIC_COURT,
            filter=filter,
        )
        eco_docs = await self.asearch(effective, eco_config)
        eco_result = await self.format_results(eco_docs)

        # Main (lexuz) portion (standard hybrid search + standard formatting)
        main_embedding = await self.embedder.aembed_query(effective)
        main_docs = await asyncio.to_thread(
            self.db.search_hybrid,
            dense_vector=main_embedding,
            text_query=effective,
            top_k=settings.ADDITIONAL_TOP_K,
            collection_name=settings.MILVUS_MAIN_NAME,
        )
        main_result = await self.formatter.format_results(main_docs)

        combined_ctx = f"{eco_result.context}\n\n{'=' * 60}\n\n{main_result.context}"
        if file_context:
            combined_ctx += f"\n\n\n{file_context}"

        return RetrievalResult(
            context=combined_ctx,
            attachments=eco_result.attachments + main_result.attachments,
        )

    # Helpers
    @staticmethod
    def _safe_float(val) -> float:
        try:
            return float(val)
        except (ValueError, TypeError):
            return 0.0
