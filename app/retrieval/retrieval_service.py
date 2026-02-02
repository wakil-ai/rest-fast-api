import re

from typing import Any
from pathlib import Path
import unicodedata

from app.core.config import settings
from app.core.logger import logger
from app.db.db_manager import DBManager
from app.retrieval.embedding_manager import EmbeddingManager
from app.services.storage_service import StorageService


class RetrievalService:
    """Handles retrieval operations from MongoDB and Milvus with dense, hybrid, and full-text search."""

    def __init__(self):
        self.db_manager = DBManager()
        self.embedding_manager = EmbeddingManager()
        self.gcp_service = StorageService()

        self.max_characters_preview = 2000  # Max characters to preview from GCP files

    async def clean_text(self, text: str) -> str:
        # Remove markdown bold (**) and italics (*)
        cleaned_text = re.sub(r"(\*\*|\*|__|_)+", "", text)

        # Remove any markdown formatting (like headers, lists, etc.)
        cleaned_text = re.sub(r"([#*-]+)", "", cleaned_text)

        # Remove URLs entirely (this will remove the links)
        cleaned_text = re.sub(r"https?://[^\s]+", "", cleaned_text)

        # Remove buxgalter.uz links
        cleaned_text = re.sub(r"buxgalter\.uz", "", cleaned_text, flags=re.IGNORECASE)

        # Remove [],(),{} from text
        cleaned_text = re.sub(r"[\[\]{}()<>]", "", cleaned_text)

        return cleaned_text

    async def retrieve_context(
        self,
        query: str,
        top_k: int = settings.TOP_K,
        alpha: float = settings.ALPHA,
        search_type: str = "hybrid",
        collection_name: str = settings.MILVUS_MAIN_NAME,
        file_context: str | None = None,
    ) -> tuple[str, list[dict[str, Any]]]:
        """Retrieve and format top documents by combining hybrid search and metadata reranking results."""
        try:
            if file_context:
                half_k = top_k // 2
                query_results = await self._retrieve_raw_documents(
                    query=query,
                    top_k=half_k,
                    alpha=alpha,
                    search_type=search_type,
                    collection_name=collection_name,
                    expr=None,
                )
                file_context_results = await self._retrieve_raw_documents(
                    query=file_context,
                    top_k=top_k,
                    alpha=alpha,
                    search_type=search_type,
                    collection_name=collection_name,
                    expr=None,
                )
                combined_results = query_results + file_context_results
                unique_results = self._deduplicate_documents(combined_results)
                # Sort by score descending and take top_k
                search_results = sorted(
                    unique_results, key=lambda x: x.get("score", 0), reverse=True
                )[:top_k]
            else:
                search_results = await self._retrieve_raw_documents(
                    query=query,
                    top_k=top_k,
                    alpha=alpha,
                    search_type=search_type,
                    collection_name=collection_name,
                    expr=None,
                )

            if collection_name == settings.MILVUS_SHARTNOMA:
                return await self._format_contract_results(search_results)

            return (await self._format_results(search_results), [])
        except Exception as e:
            logger.error(f"[RetrievalService] Retrieval failed: {e}", exc_info=True)
            if collection_name == settings.MILVUS_SHARTNOMA:
                return ("", [])
            return ("No relevant documents found.", [])

    async def _retrieve_raw_documents(
        self,
        query: str,
        top_k: int = settings.TOP_K,
        alpha: float = settings.ALPHA,
        search_type: str = "hybrid",
        collection_name: str = settings.MILVUS_MAIN_NAME,
        expr: str | None = None,
    ) -> list[dict[str, Any]]:
        """Retrieve raw documents without formatting."""
        if collection_name == settings.MILVUS_SOLIQ_ASSISTANT_NAME:
            return self.search_soliq_assistant(
                text_query=query, top_k=top_k, collection_name=collection_name
            )

        if search_type == "sparse":
            return self.search_sparse(
                text_query=query, top_k=top_k, collection_name=collection_name
            )
        elif search_type == "dense":
            return self.search_dense(
                text_query=query, top_k=top_k, collection_name=collection_name
            )
        elif search_type == "specific":
            return self.search_specific(
                text_query=query, top_k=top_k, collection_name=collection_name
            )
        else:  # Default to hybrid search
            return self.search_hybrid(
                text_query=query,
                top_k=top_k,
                alpha=alpha,
                collection_name=collection_name,
                expr=expr,
            )

    async def retrieve_multilingual(
        self,
        query_translations: dict[str, str],
        top_k: int = settings.TOP_K,
        alpha: float = settings.ALPHA,
        search_type: str = "hybrid",
        collection_name: str = settings.MILVUS_MAIN_NAME,
    ) -> list[dict[str, Any]]:
        """Retrieve and merge documents for multiple query translations."""
        all_results = []

        # We retrieve slightly more per language to ensure we have enough after deduplication
        per_lang_top_k = int(top_k * 1.5)

        for lang_code, query in query_translations.items():
            try:
                logger.info(f"[RetrievalService] Retrieving for {lang_code}: {query}")
                results = await self._retrieve_raw_documents(
                    query=query,
                    top_k=per_lang_top_k,
                    alpha=alpha,
                    search_type=search_type,
                    collection_name=collection_name,
                )
                all_results.extend(results)
            except Exception as e:
                logger.error(f"[RetrievalService] Search failed for {lang_code}: {e}")

        if not all_results:
            return []

        # Deduplicate
        unique_results = self._deduplicate_documents(all_results)

        # Sort by score descending
        sorted_results = sorted(
            unique_results, key=lambda x: x.get("score", 0), reverse=True
        )

        # Return top_k
        return sorted_results[:top_k]

    def _deduplicate_documents(
        self, documents: list[dict[str, Any]]
    ) -> list[dict[str, Any]]:
        """Deduplicate documents by chunk_url or text content hash."""
        seen_urls = set()
        unique_docs = []

        for doc in documents:
            url = doc.get("metadata", {}).get("chunk_url")
            if url:
                if url not in seen_urls:
                    seen_urls.add(url)
                    unique_docs.append(doc)
            else:
                # If no URL, keep it (or we could use text hash)
                unique_docs.append(doc)

        return unique_docs

    def search_sparse(
        self,
        text_query: str,
        top_k: int = settings.TOP_K,
        collection_name: str = settings.MILVUS_MAIN_NAME,
    ) -> list[dict[str, Any]]:
        """Perform sparse vector search using BM25 keyword relevance."""
        return self.db_manager.search_sparse(
            text_query=text_query, top_k=top_k, collection_name=collection_name
        )

    def search_dense(
        self,
        text_query: str,
        top_k: int = settings.TOP_K,
        collection_name: str = settings.MILVUS_MAIN_NAME,
    ) -> list[dict[str, Any]]:
        """Perform dense vector search using semantic embeddings."""
        embedding = self.embedding_manager.embed_query(text_query)
        return self.db_manager.search_dense(embedding, top_k, collection_name)

    def search_soliq_assistant(
        self,
        text_query: str,
        top_k: int = settings.TOP_K,
        collection_name: str = settings.MILVUS_SOLIQ_ASSISTANT_NAME,
    ) -> list[dict[str, Any]]:
        """Perform soliq assistant search using keyword matching."""
        embedding = self.embedding_manager.embed_query(text_query)
        return self.db_manager.vector_handler.query_soliq_assistant(
            dense_vector=embedding, top_k=top_k, collection_name=collection_name
        )

    def search_hybrid(
        self,
        text_query: str,
        top_k: int = settings.TOP_K,
        alpha: float = settings.ALPHA,
        collection_name: str = settings.MILVUS_MAIN_NAME,
        expr: str | None = None,
    ) -> list[dict[str, Any]]:
        """Perform hybrid search combining dense vectors and BM25 keyword relevance using Milvus."""
        embedding = self.embedding_manager.embed_query(text_query)
        return self.db_manager.search_hybrid(
            dense_vector=embedding,
            text_query=text_query,
            top_k=top_k,
            alpha=alpha,
            collection_name=collection_name,
            expr=expr or "",
        )

    async def retrieve_project_context(
        self,
        query: str,
        project_id: str,
        user_id: str,
        top_k: int = settings.TOP_K,
        alpha: float = settings.ALPHA,
    ) -> str:
        """Retrieve and format documents from a specific project."""
        try:
            expr = f'metadata["project_id"] == "{project_id}" and metadata["user_id"] == "{user_id}"'
            search_results = await self._retrieve_raw_documents(
                query=query,
                top_k=top_k,
                alpha=alpha,
                search_type="hybrid",
                collection_name=settings.MILVUS_PROJECT_FILES,
                expr=expr,
            )
            return await self._format_results(search_results)
        except Exception as e:
            logger.error(
                f"[RetrievalService] Project retrieval failed: {e}", exc_info=True
            )
            return "No relevant documents found in the project files."

    def search_specific(
        self,
        text_query: str,
        top_k: int = settings.TOP_K,
        collection_name: str = settings.MILVUS_MAIN_NAME,
    ) -> list[dict[str, Any]]:
        """Perform specific search when article number asked."""
        return self.db_manager.search_specific(
            text_query=text_query, top_k=top_k, collection_name=collection_name
        )

    def search_mongo_fulltext(
        self, collection: str, text_query: str
    ) -> list[dict[str, Any]]:
        """Perform regex-based full-text search in a MongoDB collection."""
        query = {"content": {"$regex": text_query, "$options": "i"}}
        return self._format_mongo_results(
            self.db_manager.find_documents(collection, query)
        )

    def search_mongo_metadata(
        self, collection: str, filters: dict[str, Any]
    ) -> list[dict[str, Any]]:
        """Search MongoDB documents based on metadata fields."""
        return self._format_mongo_results(
            self.db_manager.find_documents(collection, filters)
        )

    def _format_mongo_results(
        self, documents: list[dict[str, Any]]
    ) -> list[dict[str, Any]]:
        """Format MongoDB documents into a standardized response."""
        return [
            {
                "id": doc.get("id", ""),
                "content": doc.get("content", ""),
                "metadata": doc.get("metadata", {}),
                "source_url": doc.get("source_url", ""),
                "source_type": doc.get("source_type", "unknown"),
            }
            for doc in documents
        ]
    
    async def md_path_to_docx_gcs_path(self, md_path: str) -> str:
        """
        Convert markdown contract path to normalized GCS DOCX path.

        Example:
        input:
        shartnomalar/.../Ўзб/.../тўғрисида.md

        output:
        shartnomalar-docx/shartnomalar/.../Ўзб/.../тўғрисида.docx
        """

        # Normalize unicode (fix Uzbek Cyrillic combining characters)
        normalized = unicodedata.normalize("NFC", md_path)

        # Convert extension
        p = Path(normalized)

        if p.suffix.lower() != ".md":
            raise ValueError("Input must be .md file")

        docx_path = p.with_suffix(".docx")

        # Add docx bucket prefix
        final_path = Path("shartnomalar-docx") / docx_path

        # Return POSIX path (important for GCS)
        return final_path.as_posix()

    async def _format_contract_results(
        self, documents: list[dict[str, Any]]
    ) -> tuple[str, list[dict[str, Any]]]:
        """Format contract documents for shartnoma assistant.

        Notes:
        - The LLM should NOT see previews or links.
        - The client should receive up to 5 docx links as attachments.
        """

        formatted_entries: list[str] = []
        attachments: list[dict[str, Any]] = []
        seen_entries: set[str] = set()
        seen_docx_paths: set[str] = set()

        for doc in documents[:5]: # TODO: get it from settings config
            metadata = doc.get("metadata", {})
            hierarchy_path = metadata.get("hierarchy_path", "")
            if metadata.get("owner") != "wakilai":
                continue

            summary = (metadata.get("text") or "").strip()
            if summary:
                entry = f"{'-' * 50}\nSummary:\n{summary}\n"
                if entry not in seen_entries:
                    seen_entries.add(entry)
                    formatted_entries.append(entry)

            md_blob_path = metadata.get("gcs_md_path", "")
            docx_blob_path = await self.md_path_to_docx_gcs_path(md_blob_path)
            
            public_url = self.gcp_service.get_signed_url(docx_blob_path)
            
            attachments.append(
                {
                    "name": hierarchy_path.split("/")[-1] + ".docx",
                    "url": public_url,
                    "content_type": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
                }
            )
            seen_docx_paths.add(docx_blob_path)
            if len(attachments) >= 3:
                break

        return ("\n".join(formatted_entries).strip(), attachments)

    async def _format_results(self, documents: list[dict[str, Any]]) -> str:
        """Format documents into a readable string."""
        formatted_entries = []
        seen_content = set()

        for doc in documents:
            metadata = doc.get("metadata", {})
            entry = await self._build_document_entry(
                metadata=metadata,
            )
            score = doc.get("score", 0)
            entry = f"Relevance Score: {score:.4f}\n{entry}"

            if entry not in seen_content:
                seen_content.add(entry)
                formatted_entries.append(entry)

        return "\n".join(formatted_entries)

    def remove_header_lines(self, text: str) -> str:
        """
        Removes entire markdown header lines (#, ##, ###, ####).
        """
        cleaned_lines = []
        for line in text.splitlines():
            if not re.match(r"^\s*#{1,6}\s*", line):  # skip header lines
                cleaned_lines.append(line)
        return "\n".join(cleaned_lines).strip()

    async def _build_document_entry(self, metadata: dict[str, Any]) -> str:
        """Build a formatted entry for a single document."""
        text = metadata.get("text", "")
        url = metadata.get("url")

        is_buxgalter_uz = True if url and "buxgalter.uz" in url else False
        text = await self.clean_text(text) if is_buxgalter_uz else text
        entry = [f"{'-' * 50}", f"Document Content: {self.remove_header_lines(text)}\n"]

        # Add citation
        if hierarchy := metadata.get("hierarchy_path"):
            if is_buxgalter_uz:
                pass
            else:
                parts = [p.strip() for p in hierarchy.split(">")]
                seen = set()
                ordered_unique = []
                for p in parts:
                    if p and p not in seen:
                        seen.add(p)
                        ordered_unique.append(p)
                citation = ". ".join(ordered_unique)
                entry.append(f"Citation: {citation}\n")

        if date := metadata.get("date"):
            if not is_buxgalter_uz:
                entry.append(f"Date: {date}\n")
        if url := metadata.get("chunk_url"):
            if not is_buxgalter_uz:
                entry.append(f"Source URL: {url}\n")
        elif metadata.get("project_id"):  # if project id is there
            if filename := metadata.get("file_name"):
                entry.append(f"Source: Project File - {filename}\n")
            else:
                entry.append("Source: Project Document\n")
        else:
            if filename := metadata.get("file_name"):
                # Remove .md at the end
                filename = filename.replace(".md", "")
                suffix = "https://lex.uz/docs/"
                if await self._is_valid_id(filename):
                    entry.append(f"Source: {suffix + filename}\n")
                else:
                    entry.append("Source: WakilAI ichki hujjatlari")
        if document_number := metadata.get("document_number"):
            entry.append(f"Document Number: {document_number}\n")

        return "\n".join(entry)

    async def _is_valid_id(self, s: str) -> bool:
        if not isinstance(s, str):
            return False
        return re.fullmatch(r"-?\d+", s) is not None
