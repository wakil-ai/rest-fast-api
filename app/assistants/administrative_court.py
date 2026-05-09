from __future__ import annotations

import asyncio
import os
from typing import Any

from app.assistants.base import BaseAssistant, RetrievalConfig, RetrievalResult
from app.core.assistants import AssistantConfig
from app.core.config import settings
from app.core.dependencies import get_milvus_query_agent, get_prompt_registry
from app.core.logger import logger
from app.utils.text_cleaning import clean_pdf_html_text


class AdministrativeCourtAssistant(BaseAssistant):
    """Administrative-court assistant with file-level retrieval and domain routing.

    Owns its own:
    - Specialist prompt from ``court_route_tag`` (set by ``CourtClassifier`` when assistant=court)
    - Milvus filter generation (court/instance/category)
    - File-level search expansion
    - Court-specific metadata formatting
    """

    # court_route_tag (from CourtClassifier) → prompts_registry key
    ADMINISTRATIVE_ROUTE_TO_PROMPT: dict[str, str] = {
        "administrative_tax_predicting_lawsuit": "predicting_lawsuit_result",
        "administrative_tax_appeal_tax_admin": "appeal_tax_administration",
        "administrative_tax_appeal_court_decision": "appeal_court_decision",
        "administrative_general_admin_litigation": "supreme_admin_litigation",
        "administrative_general_judicial_review": "supreme_judicial_review",
    }
    DEFAULT_COURT_ROUTE_TAG = "administrative_general_admin_litigation"

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
            collection_name=settings.MILVUS_ADMINISTRATIVE_COURT_ALL,
            top_k=settings.TOP_K,
        )
        self.milvus_agent = get_milvus_query_agent()

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
        1. Prompt from ``court_route_tag`` (court router) or default administrative litigation
        2. Generate Milvus filter expression
        3. Route to tax vs general multi-collection merge
        """
        try:
            court_route_tag = kwargs.get("court_route_tag")
            template = self._prompt_template_for_route(court_route_tag)
            domain_type = (
                "tax"
                if (court_route_tag or "").startswith("administrative_tax_")
                else "general"
            )
            logger.info(
                "[AdministrativeCourtAssistant] court_route_tag=%s → domain=%s",
                court_route_tag,
                domain_type,
            )

            # Milvus filter expression (court, instance, category)
            milvus_filter = await self.milvus_agent.generate_filter(
                query,
                chat_history,
                file_context,
                assistant="administrative_court",
            )

            # Route by domain
            if domain_type == "tax":
                result = await self._retrieve_tax(query, file_context, milvus_filter)
            else:
                result = await self._retrieve_general(
                    query, file_context, milvus_filter
                )

            result.prompt_template = template
            return result

        except Exception as e:
            logger.error(
                f"[AdministrativeCourtAssistant] Retrieval failed: {e}", exc_info=True
            )
            return self._error_result()

    def _prompt_template_for_route(self, court_route_tag: str | None):
        registry = get_prompt_registry()
        tag = court_route_tag or self.DEFAULT_COURT_ROUTE_TAG
        if not tag.startswith("administrative_"):
            tag = self.DEFAULT_COURT_ROUTE_TAG
        prompt_key = self.ADMINISTRATIVE_ROUTE_TO_PROMPT.get(tag)
        if not prompt_key:
            logger.warning(
                "[AdministrativeCourtAssistant] Unknown court_route_tag=%s → %s",
                court_route_tag,
                self.DEFAULT_COURT_ROUTE_TAG,
            )
            prompt_key = self.ADMINISTRATIVE_ROUTE_TO_PROMPT[self.DEFAULT_COURT_ROUTE_TAG]
        return registry.get_prompt(prompt_key)

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
            file_text = clean_pdf_html_text(
                file_text
            )  # Cleaning HTML artifacts from PDF extraction (common in court documents)
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

    # Domain-specific retrieval (multi-collection merging)
    async def _retrieve_tax(
        self, query: str, file_context: str, filter: str
    ) -> RetrievalResult:
        """Merge results from administrative court + tax collections."""
        # Administrative-court portion
        mam_config = RetrievalConfig(
            top_k=settings.ADDITIONAL_TOP_K,
            collection_name=settings.MILVUS_ADMINISTRATIVE_COURT_ALL,
            filter=filter,
        )
        effective = f"{query}\n\n\n{file_context}" if file_context else query
        mam_docs = await self.asearch(effective, mam_config)
        mam_result = await self.format_results(mam_docs)

        # tax portion (standard hybrid search + standard formatting)
        tax_coll = AssistantConfig.get_collection_name("tax")
        tax_embedding = await self.embedder.aembed_query(effective)
        tax_docs = await asyncio.to_thread(
            self.db.search_hybrid,
            dense_vector=tax_embedding,
            text_query=effective,
            top_k=settings.ADDITIONAL_TOP_K,
            collection_name=tax_coll,
        )
        tax_result = await self.formatter.format_results(tax_docs)

        combined_ctx = f"{mam_result.context}\n\n{'=' * 60}\n\n{tax_result.context}"
        if file_context:
            combined_ctx += f"\n\n\n{file_context}"

        return RetrievalResult(
            context=combined_ctx,
            attachments=mam_result.attachments + tax_result.attachments,
        )

    async def _retrieve_general(
        self, query: str, file_context: str, filter: str
    ) -> RetrievalResult:
        """Merge results from administrative court + main (lexuz) collections."""
        effective = f"{query}\n\n\n{file_context}" if file_context else query

        # Administrative-court portion
        mam_config = RetrievalConfig(
            top_k=settings.TOP_K,
            collection_name=settings.MILVUS_ADMINISTRATIVE_COURT_ALL,
            filter=filter,
        )
        mam_docs = await self.asearch(effective, mam_config)
        mam_result = await self.format_results(mam_docs)

        # main (lexuz) portion (standard hybrid search + standard formatting)
        main_embedding = await self.embedder.aembed_query(effective)
        main_docs = await asyncio.to_thread(
            self.db.search_hybrid,
            dense_vector=main_embedding,
            text_query=effective,
            top_k=settings.ADDITIONAL_TOP_K,
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
