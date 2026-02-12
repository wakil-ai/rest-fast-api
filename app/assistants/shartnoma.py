from __future__ import annotations

from pathlib import Path
from typing import Any, Optional

from app.assistants.base import BaseAssistant, RetrievalConfig, RetrievalResult
from app.chains.intent_classifier import IntentClassifier
from app.core.config import settings
from app.core.logger import logger
from app.services.storage_service import StorageService
from app.utils.text_cleaning import TextCleaner


class ShartnomaAssistant(BaseAssistant):
    """Contract assistant with intent classification and attachment-aware formatting.

    Owns its own:
    - Intent classification (template generation vs risk analysis)
    - DOCX attachment handling via GCS signed URLs
    """

    def __init__(self):
        super().__init__(collection_name=settings.MILVUS_SHARTNOMA)
        self.storage_service = StorageService()
        self.intent_classifier = IntentClassifier()

    # Retrieve — classification + retrieval (all internal)
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
        2. Standard hybrid search + contract formatting
        """
        try:
            # Intent classification (template generation vs risk analysis)
            domain_type, template = await self.intent_classifier.classify_intent(
                query, chat_history, file_context
            )
            logger.info(f"[ShartnomaAssistant] Contract intent classified as domain: {domain_type}")

            # Standard retrieval pipeline
            effective_query = f"{query}\n\n\n{file_context}" if file_context else query
            config = self._build_config()

            raw_docs = self.search(effective_query, config)
            result = await self.format_results(raw_docs)

            if file_context and result.context:
                result.context += f"\n\n\n{file_context}"

            result.prompt_template = template
            return result

        except Exception as e:
            logger.error(f"[ShartnomaAssistant] Retrieval failed: {e}", exc_info=True)
            return self._error_result()

    # Search: inherits default hybrid search from BaseAssistant

    # Formatting
    async def format_results(
        self, documents: list[dict[str, Any]], max_attachments: int = 3, top_k: int = 5,
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
        content_type = (
            "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
        )
        docx_blob_path = metadata.get("gcs_docx_path", "")

        if docx_blob_path:
            public_url = self.storage_service.get_signed_url(docx_blob_path)
            hierarchy_path = metadata.get("hierarchy_path", "")
            filename = hierarchy_path.split("/")[-1] + ".docx"
            return {"name": filename, "url": public_url, "content_type": content_type}

        md_blob_path = metadata.get("gcs_md_path", "")
        if not md_blob_path:
            return None

        try:
            docx_blob_path = self._md_to_docx_gcs_path(md_blob_path)
            public_url = self.storage_service.get_signed_url(docx_blob_path)
            hierarchy_path = metadata.get("hierarchy_path", "")
            filename = hierarchy_path.split("/")[-1] + ".docx"
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
