from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any, Optional

from app.assistants.base import BaseAssistant, RetrievalResult
from app.core.config import settings
from app.core.dependencies import get_intent_classifier, get_storage_service
from app.core.logger import logger
from app.utils.text_cleaning import TextCleaner


class ContractAnalyzerAssistant(BaseAssistant):
    """Contract analyzer assistant with intent classification and attachment-aware formatting.

    Owns its own:
    - Intent classification (template generation vs risk analysis)
    - DOCX attachment handling via GCS signed URLs
    """

    def __init__(self):
        super().__init__(collection_name=settings.MILVUS_CONTRACT_ANALYZER)
        self.storage_service = get_storage_service()
        self.intent_classifier = get_intent_classifier()

    # Retrieve — classification + multi-collection retrieval
    async def retrieve(
        self,
        query: str,
        file_context: str = "",
        *,
        chat_history: str = "",
        **kwargs,
    ) -> RetrievalResult:
        """
        1. Classify intent → domain_type + prompt template
        2. Retrieve full top_k from contract analyzer collection (contract formatting)
        3. Retrieve half top_k from main/lexuz (standard formatting)
        4. Merge both results
        """
        try:
            # Intent classification (template generation vs risk analysis)
            domain_type, template, legal_intent = (
                await self.intent_classifier.classify_intent(
                    query, chat_history, file_context
                )
            )
            logger.info(
                f"[ContractAnalyzerAssistant] Contract intent classified as domain: {domain_type}"
            )

            effective_query = f"{query}\n\n\n{file_context}" if file_context else query

            # Contract-analyzer portion — full top_k, contract formatting
            contract_config = self._build_config()
            contract_docs = await self.asearch(effective_query, contract_config)
            contract_result = await self.format_results(contract_docs)

            # Main (lexuz) portion — half top_k, standard formatting
            logger.info(
                f"[ContractAnalyzerAssistant] Retrieving {self.top_k} from contract_analyzer + {settings.ADDITIONAL_TOP_K} from main"
            )
            main_embedding = await self.embedder.aembed_query(effective_query)
            main_docs = await asyncio.to_thread(
                self.db.search_hybrid,
                dense_vector=main_embedding,
                text_query=effective_query,
                top_k=settings.ADDITIONAL_TOP_K,
                collection_name=settings.MILVUS_MAIN_NAME,
            )
            main_result = await self.formatter.format_results(main_docs)

            # Merge results
            combined_ctx = contract_result.context
            if main_result.context:
                combined_ctx += f"\n\n{'=' * 60}\n\n{main_result.context}"
            if file_context and combined_ctx:
                combined_ctx += f"\n\n\n{file_context}"

            result = RetrievalResult(
                context=combined_ctx,
                attachments=contract_result.attachments,
                prompt_template=template,
                classified_legal_intent=legal_intent.value,
            )
            return result

        except Exception as e:
            logger.error(
                f"[ContractAnalyzerAssistant] Retrieval failed: {e}", exc_info=True
            )
            return self._error_result()

    # Search: inherits default hybrid search from BaseAssistant

    # Formatting
    async def format_results(
        self,
        documents: list[dict[str, Any]],
        max_attachments: int = 1,
        top_k: int = settings.TOP_K,
    ) -> RetrievalResult:
        """Format contract documents and generate DOCX download attachments."""
        entries: list[str] = []
        attachments: list[dict[str, Any]] = []
        seen_entries: set[str] = set()
        seen_docx_paths: set[str] = set()

        for doc in documents[:top_k]:
            metadata = doc.get("metadata", {})

            summary = (metadata.get("text") or "").strip()
            if summary:
                entry = f"{'-' * 50}\nSummary:\n{summary}\n"
                if entry not in seen_entries:
                    seen_entries.add(entry)
                    entries.append(entry)

            score_raw = doc.get("score")
            try:
                score = float(score_raw) if score_raw is not None else None
            except (TypeError, ValueError):
                score = None
            if score is not None and score < settings.CONTRACT_ATTACHMENT_MIN_SIMILARITY:
                continue

            if (
                len(attachments) < max_attachments
                and metadata.get("owner") == "wakilai"
            ):
                att = await self._create_attachment(metadata)
                if att and att["url"] not in seen_docx_paths:
                    attachments.append(att)
                    seen_docx_paths.add(att["url"])

        return RetrievalResult(
            context="\n".join(entries),
            attachments=attachments,
        )

    # Helpers
    async def _create_attachment(
        self, metadata: dict[str, Any]
    ) -> Optional[dict[str, Any]]:
        content_type_map = {
            "docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            "doc": "application/msword",
            "pdf": "application/pdf",
        }
        blob_path = metadata.get("gcs_docx_path", metadata.get("gcs_file_path", ""))
        file_full_path = metadata.get("file_full_path", "")
        ext = Path(file_full_path).suffix.lower()[1:]

        logger.info(f"Creating attachment for file: {file_full_path} with ext: {ext}")

        content_type = content_type_map.get(ext, "")

        if blob_path:
            public_url = self.storage_service.get_signed_url(blob_path)
            hierarchy_path = metadata.get("hierarchy_path", "")
            filename = hierarchy_path.split("/")[-1] + f".{ext}"
            return {"name": filename, "url": public_url, "content_type": content_type}

        md_blob_path = metadata.get("gcs_md_path", "")
        if not md_blob_path:
            return None

        try:
            blob_path = self._md_to_docx_gcs_path(md_blob_path)
            public_url = self.storage_service.get_signed_url(blob_path)
            hierarchy_path = metadata.get("hierarchy_path", "")
            filename = hierarchy_path.split("/")[-1] + f".{ext}"
            return {"name": filename, "url": public_url, "content_type": content_type}
        except Exception as e:
            logger.error(f"Failed to create attachment: {e}")
            return None

    @staticmethod
    def _md_to_docx_gcs_path(md_path: str) -> str:
        """Convert markdown contract path to GCS DOCX path."""
        normalized = TextCleaner.normalize_unicode(md_path)
        p = Path(normalized)
        if p.suffix.lower() != ".md":
            raise ValueError("Input must be .md file")
        docx_path = p.with_suffix(".docx")
        return (Path("shartnomalar-docx") / docx_path).as_posix()
