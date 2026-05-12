from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any, Optional

from langchain_core.tools import tool

from app.agents.base import BaseAgent, RetrievalResult
from app.agents.common.state import AgentRequestContext, AgentState
from app.core.config import settings
from app.core.dependencies import get_intent_classifier, get_storage_service
from app.core.logger import logger
from app.models.intent_types import LegalIntent
from app.utils.text_cleaning import TextCleaner


class ContractAnalyzerAgent(BaseAgent):
    """Contract analyzer with intent classification, multi-collection retrieval, and DOCX output.

    Tool flow:
    1. ``classify_contract_intent`` → sets state.classified_legal_intent
    2. ``search_contract_corpus`` → retrieves templates + legal statutes, accumulates attachments
    3. ``attach_outputs`` post-processes DOCX if intent is template_generation
    """

    def __init__(self):
        super().__init__(
            collection_name=settings.MILVUS_CONTRACT_ANALYZER,
            assistant_name="contract_analyzer",
        )
        self.storage_service = get_storage_service()
        self.intent_classifier = get_intent_classifier()

    def _context_guidance_text(self) -> str:
        return (
            "First call `classify_contract_intent` to determine the intent: "
            "template_generation or risk_analysis. "
            "Then use `search_contract_corpus` to retrieve relevant contract templates and "
            "legal statutes. "
            "Use `get_chat_history` for conversation context. "
            "Uploaded file content (when present) is provided directly below — analyze it "
            "directly for risk_analysis intents; only call `get_uploaded_file_context` for "
            "refined keyword searches across the same files. "
            "For template generation: produce a complete contract draft. "
            "For risk analysis: provide a structured risk assessment."
        )

    def _build_domain_tools(
        self, request: AgentRequestContext, state: AgentState
    ) -> list[Any]:
        @tool
        async def classify_contract_intent(query: str) -> str:
            """Classify the contract request intent: template_generation or risk_analysis."""
            try:
                _, _, legal_intent = await self.intent_classifier.classify_intent(
                    query, "", ""
                )
                state.classified_legal_intent = legal_intent.value
                return f"Contract intent: {legal_intent.value}"
            except Exception as exc:
                logger.warning(f"classify_contract_intent failed: {exc}", exc_info=True)
                return "Intent classification failed; proceed with contract analysis."

        @tool
        async def search_contract_corpus(query: str) -> str:
            """Search contract templates and legal statutes for contract-related content."""
            try:
                result = await self._retrieve_contracts(query)
                if result.attachments:
                    state.attachments = (
                        list(state.attachments or []) + result.attachments
                    )
                return result.context or "No relevant contract documents found."
            except Exception as exc:
                logger.warning(f"search_contract_corpus failed: {exc}", exc_info=True)
                return "Contract search failed; answer from general reasoning."

        return [classify_contract_intent, search_contract_corpus]

    async def _retrieve_contracts(self, query: str) -> RetrievalResult:
        """Multi-collection retrieval: contract templates + LexUZ statutes."""
        contract_docs = await self.asearch(query, self._build_config())
        contract_result = await self.format_results(contract_docs)

        main_embedding = await self.embedder.aembed_query(query)
        main_docs = await asyncio.to_thread(
            self.db.search_hybrid,
            dense_vector=main_embedding,
            text_query=query,
            top_k=settings.ADDITIONAL_TOP_K,
            collection_name=settings.MILVUS_MAIN_NAME,
        )
        main_result = await self.formatter.format_results(main_docs)

        combined_ctx = contract_result.context
        if main_result.context:
            combined_ctx += f"\n\n{'=' * 60}\n\n{main_result.context}"
        return RetrievalResult(
            context=combined_ctx,
            attachments=contract_result.attachments,
        )

    async def attach_outputs(
        self, answer: str, state: AgentState
    ) -> list[dict[str, Any]]:
        if (
            state.classified_legal_intent
            != LegalIntent.CONTRACT_TEMPLATE_GENERATION.value
        ):
            return []
        return await self.upload_contract_docx(
            user_id=state.request.user_id,
            full_answer=answer,
            existing_attachments=state.attachments,
        )

    # retrieve() kept for backward-compat / debug state snapshots
    async def retrieve(
        self,
        query: str,
        file_context: str = "",
        *,
        chat_history: str = "",
        **kwargs,
    ) -> RetrievalResult:
        try:
            domain_type, template, legal_intent = (
                await self.intent_classifier.classify_intent(
                    query, chat_history, file_context
                )
            )
            logger.info(
                f"[ContractAnalyzerAgent] Contract intent classified as domain: {domain_type}"
            )

            contract_config = self._build_config()
            contract_docs = await self.asearch(query, contract_config)
            contract_result = await self.format_results(contract_docs)

            logger.info(
                f"[ContractAnalyzerAgent] Retrieving {self.top_k} from contract_analyzer "
                f"+ {settings.ADDITIONAL_TOP_K} from main"
            )
            main_embedding = await self.embedder.aembed_query(query)
            main_docs = await asyncio.to_thread(
                self.db.search_hybrid,
                dense_vector=main_embedding,
                text_query=query,
                top_k=settings.ADDITIONAL_TOP_K,
                collection_name=settings.MILVUS_MAIN_NAME,
            )
            main_result = await self.formatter.format_results(main_docs)

            combined_ctx = contract_result.context
            if main_result.context:
                combined_ctx += f"\n\n{'=' * 60}\n\n{main_result.context}"
            if file_context and combined_ctx:
                combined_ctx += f"\n\n\n{file_context}"

            retrieval_attachments = (
                contract_result.attachments
                if legal_intent != LegalIntent.CONTRACT_RISK_ANALYSIS
                else []
            )

            return RetrievalResult(
                context=combined_ctx,
                attachments=retrieval_attachments,
                prompt_template=template,
                classified_legal_intent=legal_intent.value,
            )

        except Exception as e:
            logger.error(
                f"[ContractAnalyzerAgent] Retrieval failed: {e}", exc_info=True
            )
            return self._error_result()

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
            if (
                score is not None
                and score < settings.CONTRACT_ATTACHMENT_MIN_SIMILARITY
            ):
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
