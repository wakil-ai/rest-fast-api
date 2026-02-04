import re
import unicodedata
from pathlib import Path
from typing import Any, Optional

from app.core.logger import logger
from app.core.config import settings
from app.services.storage_service import StorageService


class TextCleaner:
    """Handles text cleaning and normalization."""

    @staticmethod
    def clean_markdown(text: str) -> str:
        """Remove markdown formatting from text."""
        # Remove bold and italics
        text = re.sub(r"(\*\*|\*|__|_)+", "", text)
        # Remove headers, lists markers
        text = re.sub(r"([#*-]+)", "", text)
        # Remove URLs
        text = re.sub(r"https?://[^\s]+", "", text)
        # Remove buxgalter.uz references
        text = re.sub(r"buxgalter\.uz", "", text, flags=re.IGNORECASE)
        # Remove brackets
        text = re.sub(r"[\[\]{}()<>]", "", text)
        return text

    @staticmethod
    def remove_header_lines(text: str) -> str:
        """Remove entire markdown header lines."""
        cleaned_lines = [
            line for line in text.splitlines() if not re.match(r"^\s*#{1,6}\s*", line)
        ]
        return "\n".join(cleaned_lines).strip()

    @staticmethod
    def normalize_unicode(text: str) -> str:
        """Normalize unicode characters (useful for Uzbek Cyrillic)."""
        return unicodedata.normalize("NFC", text)


class PathConverter:
    """Handles path conversions for different document types."""

    @staticmethod
    def md_to_docx_gcs_path(md_path: str) -> str:
        """
        Convert markdown contract path to normalized GCS DOCX path.

        Example:
            shartnomalar/.../file.md -> shartnomalar-docx/shartnomalar/.../file.docx
        """
        normalized = TextCleaner.normalize_unicode(md_path)
        p = Path(normalized)

        if p.suffix.lower() != ".md":
            raise ValueError("Input must be .md file")

        docx_path = p.with_suffix(".docx")
        final_path = Path("shartnomalar-docx") / docx_path

        return final_path.as_posix()

    @staticmethod
    def is_valid_lex_id(s: str) -> bool:
        """Check if string is a valid lex.uz document ID."""
        if not isinstance(s, str):
            return False
        return re.fullmatch(r"-?\d+", s) is not None


class DocumentDeduplicator:
    """Handles document deduplication logic."""

    @staticmethod
    def deduplicate_by_url(documents: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """Deduplicate documents by chunk_url."""
        seen_urls = set()
        unique_docs = []

        for doc in documents:
            url = doc.get("metadata", {}).get("chunk_url")
            if url:
                if url not in seen_urls:
                    seen_urls.add(url)
                    unique_docs.append(doc)
            else:
                # Keep documents without URL
                unique_docs.append(doc)

        return unique_docs


class DocumentFormatter:
    """Handles formatting of retrieved documents."""

    def __init__(self):
        self.storage_service = StorageService()
        self.text_cleaner = TextCleaner()
        self.path_converter = PathConverter()
        
    async def format_results(self, documents: list[dict[str, Any]], collection_name: str) -> Any:
        """Format documents based on collection type."""
        if collection_name == settings.MILVUS_SHARTNOMA:
            return await self.format_contract_results(documents)
        elif collection_name == settings.MILVUS_MAMURIY_SUD:
            return await self.format_sud_results(documents)
        else:
            return await self.format_standard_results(documents)
        
    async def format_standard_results(self, documents: list[dict[str, Any]]) -> str:
        """Format standard documents into readable string."""
        formatted_entries = []
        seen_content = set()

        for doc in documents:
            entry = await self._build_document_entry(doc)

            if entry not in seen_content:
                seen_content.add(entry)
                formatted_entries.append(entry)

        return "\n".join(formatted_entries)

    async def format_contract_results(
        self, documents: list[dict[str, Any]], max_attachments: int = 3, top_k: int = 5
    ) -> tuple[str, list[dict[str, Any]]]:
        """
        Format contract documents for shartnoma assistant.

        Returns:
            Tuple of (formatted_text, attachments_list)
        """
        formatted_entries = []
        attachments = []
        seen_entries = set()
        seen_docx_paths = set()

        for doc in documents[:top_k]:
            metadata = doc.get("metadata", {})

            # Add summary if available
            summary = (metadata.get("text") or "").strip()
            if summary:
                entry = f"{'-' * 50}\nSummary:\n{summary}\n"
                if entry not in seen_entries:
                    seen_entries.add(entry)
                    formatted_entries.append(entry)

            # Create attachment if not at limit
            if (
                len(attachments) < max_attachments
                and metadata.get("owner") == "wakilai"
            ):
                attachment = await self._create_contract_attachment(metadata)
                if attachment and attachment["url"] not in seen_docx_paths:
                    attachments.append(attachment)
                    seen_docx_paths.add(attachment["url"])

        return ("\n".join(formatted_entries).strip(), attachments)
    
    async def format_sud_results(
        self, documents: list[dict[str, Any]]) -> str:
        """Format sud documents into readable string."""
        formatted_entries = []
        seen_content = set()

        for doc in documents:
            metadata = doc.get("metadata", {})
            title = metadata.get("hierarchy", "")
            
            entry_parts = [
                f"{'-' * 50}",
                f"Title: {title}",
                f"Document Content: {doc.get('text', '')}\n",
            ]
            entry = "\n".join(entry_parts)
        
            if entry not in seen_content:
                seen_content.add(entry)
                formatted_entries.append(entry)

        return "\n".join(formatted_entries)
        
    async def _create_contract_attachment(
        self, metadata: dict[str, Any]
    ) -> Optional[dict[str, Any]]:
        """Create attachment dictionary for contract document."""
        md_blob_path = metadata.get("gcs_md_path", "")
        if not md_blob_path:
            return None

        try:
            docx_blob_path = self.path_converter.md_to_docx_gcs_path(md_blob_path)
            public_url = self.storage_service.get_signed_url(docx_blob_path)

            hierarchy_path = metadata.get("hierarchy_path", "")
            filename = hierarchy_path.split("/")[-1] + ".docx"

            return {
                "name": filename,
                "url": public_url,
                "content_type": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            }
        except Exception as e:
            logger.error(f"Failed to create attachment: {e}")
            return None

    async def _build_document_entry(self, doc: dict[str, Any]) -> str:
        """Build formatted entry for a single document."""
        metadata = doc.get("metadata", {})
        score = doc.get("score", 0)

        # Get and clean text
        text = metadata.get("text", "")
        url = metadata.get("url", "")
        is_buxgalter_uz = "buxgalter.uz" in url

        if is_buxgalter_uz:
            text = self.text_cleaner.clean_markdown(text)

        text = self.text_cleaner.remove_header_lines(text)

        # Build entry parts
        entry_parts = [
            f"{'-' * 50}",
            f"Relevance Score: {score:.4f}",
            f"Document Content: {text}\n",
        ]

        # Add metadata
        entry_parts.extend(self._build_metadata_section(metadata, is_buxgalter_uz))

        return "\n".join(entry_parts)

    def _build_metadata_section(
        self, metadata: dict[str, Any], is_buxgalter_uz: bool
    ) -> list[str]:
        """Build metadata section of document entry."""
        parts = []

        # Citation
        if not is_buxgalter_uz and (hierarchy := metadata.get("hierarchy_path")):
            citation = self._build_citation(hierarchy)
            if citation:
                parts.append(f"Citation: {citation}\n")

        # Date
        if not is_buxgalter_uz and (date := metadata.get("date")):
            parts.append(f"Date: {date}\n")

        # Source
        source = self._build_source(metadata, is_buxgalter_uz)
        if source:
            parts.append(source)

        # Document number
        if doc_num := metadata.get("document_number"):
            parts.append(f"Document Number: {doc_num}\n")

        return parts

    def _build_citation(self, hierarchy: str) -> str:
        """Build citation from hierarchy path."""
        parts = [p.strip() for p in hierarchy.split(">")]
        seen = set()
        unique_parts = [p for p in parts if p and p not in seen and not seen.add(p)]
        return ". ".join(unique_parts)

    def _build_source(self, metadata: dict[str, Any], is_buxgalter_uz: bool) -> str:
        """Build source information string."""
        if is_buxgalter_uz:
            return ""

        # Check for chunk URL
        if chunk_url := metadata.get("chunk_url"):
            return f"Source URL: {chunk_url}\n"

        # Check for project context
        if metadata.get("project_id"):
            filename = metadata.get("file_name", "")
            if filename:
                return f"Source: Project File - {filename}\n"
            return "Source: Project Document\n"

        # Check for lex.uz document
        if filename := metadata.get("file_name"):
            filename = filename.replace(".md", "")
            if self.path_converter.is_valid_lex_id(filename):
                return f"Source: https://lex.uz/docs/{filename}\n"
            return "Source: WakilAI ichki hujjatlari\n"

        return ""
