from __future__ import annotations

from typing import Any

from app.models.retrieval_models import RetrievalResult
from app.utils.text_cleaning import TextCleaner, PathConverter


class StandardContextFormatter:
    def __init__(self):
        self.text_cleaner = TextCleaner()
        self.path_converter = PathConverter()

    async def format_results(self, documents: list[dict[str, Any]]) -> RetrievalResult:
        """Format raw documents with standard score + text + citation layout."""
        return await self._format_standard(documents)

    # Shared formatting standards
    async def _format_standard(self, documents: list[dict[str, Any]]) -> RetrievalResult:
        """Default formatting: score + text + citation metadata."""
        entries: list[str] = []
        seen: set[str] = set()

        for doc in documents:
            entry = self._build_standard_entry(doc)
            if entry not in seen:
                seen.add(entry)
                entries.append(entry)

        return RetrievalResult(
            context="\n\n".join(entries),
            attachments=[],
        )

    def _build_standard_entry(self, doc: dict[str, Any]) -> str:
        metadata = doc.get("metadata", {})
        score = doc.get("score", 0)

        text = metadata.get("text", "")
        url = metadata.get("url", "")
        is_buxgalter = "buxgalter.uz" in url

        if is_buxgalter:
            text = self.text_cleaner.clean_markdown(text)
        text = self.text_cleaner.remove_header_lines(text)

        parts = [
            f"{'-' * 50}",
            f"Relevance Score: {score:.4f}",
            f"Document Content: {text}\n",
        ]
        parts.extend(self._build_metadata_section(metadata, is_buxgalter))
        return "\n".join(parts)

    def _build_metadata_section(self, metadata: dict[str, Any], is_buxgalter: bool) -> list[str]:
        parts: list[str] = []

        if not is_buxgalter and (hierarchy := metadata.get("hierarchy_path")):
            citation = self._build_citation(hierarchy)
            if citation:
                parts.append(f"Citation: {citation}\n")

        if not is_buxgalter and (date := metadata.get("date")):
            parts.append(f"Date: {date}\n")

        source = self._build_source(metadata, is_buxgalter)
        if source:
            parts.append(source)

        if doc_num := metadata.get("document_number"):
            parts.append(f"Document Number: {doc_num}\n")

        return parts

    @staticmethod
    def _build_citation(hierarchy: str) -> str:
        parts = [p.strip() for p in hierarchy.split(">")]
        seen: set[str] = set()
        unique = [p for p in parts if p and p not in seen and not seen.add(p)]
        return ". ".join(unique)

    def _build_source(self, metadata: dict[str, Any], is_buxgalter: bool) -> str:
        if is_buxgalter:
            return ""

        if chunk_url := metadata.get("chunk_url"):
            return f"Source URL: {chunk_url}\n"

        if metadata.get("project_id"):
            filename = metadata.get("file_name", "")
            return f"Source: Project File - {filename}\n" if filename else "Source: Project Document\n"

        if filename := metadata.get("file_name"):
            filename = filename.replace(".md", "")
            if self.path_converter.is_valid_lex_id(filename):
                return f"Source: https://lex.uz/docs/{filename}\n"
            return "Source: WakilAI ichki hujjatlari\n"

        return ""

    @staticmethod
    def deduplicate_by_url(documents: list[dict[str, Any]]) -> list[dict[str, Any]]:
        seen: set[str] = set()
        unique: list[dict[str, Any]] = []
        for doc in documents:
            url = doc.get("metadata", {}).get("chunk_url")
            if url:
                if url not in seen:
                    seen.add(url)
                    unique.append(doc)
            else:
                unique.append(doc)
        return unique