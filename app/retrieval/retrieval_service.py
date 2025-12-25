from typing import List, Dict, Any
from app.core.config import settings
from app.core.logger import logger
from app.db.db_manager import DBManager
from app.retrieval.embedding_manager import EmbeddingManager
import re


class RetrievalService:
    """Handles retrieval operations from MongoDB and Milvus with dense, hybrid, and full-text search."""

    def __init__(self):
        self.db_manager = DBManager()
        self.embedding_manager = EmbeddingManager()

    async def clean_text(self, text: str) -> str:
        # Remove markdown bold (**) and italics (*)
        cleaned_text = re.sub(r'(\*\*|\*|__|_)+', '', text)
        
        # Remove any markdown formatting (like headers, lists, etc.)
        cleaned_text = re.sub(r'([#*-]+)', '', cleaned_text)
        
        # Remove URLs entirely (this will remove the links)
        cleaned_text = re.sub(r'https?://[^\s]+', '', cleaned_text)
        
        # Remove buxgalter.uz links
        cleaned_text = re.sub(r'buxgalter\.uz', '', cleaned_text, flags=re.IGNORECASE)

        # Remove [],(),{} from text
        cleaned_text = re.sub(r'[\[\]{}()<>]', '', cleaned_text)

        return cleaned_text
                
    async def retrieve_context(
        self,
        query: str,
        top_k: int = settings.TOP_K,
        alpha: float = settings.ALPHA,
        search_type: str = "hybrid",
        collection_name: str = settings.MILVUS_MAIN_NAME,
    ) -> str:
        """Retrieve and format top documents by combining hybrid search and metadata reranking results."""
        try:
            search_results = await self._retrieve_raw_documents(
                query=query,
                top_k=top_k,
                alpha=alpha,
                search_type=search_type,
                collection_name=collection_name
            )
            return await self._format_results(search_results)
        except Exception as e:
            logger.error(f"[RetrievalService] Retrieval failed: {e}", exc_info=True)
            return "No relevant documents found."

    async def _retrieve_raw_documents(
        self,
        query: str,
        top_k: int = settings.TOP_K,
        alpha: float = settings.ALPHA,
        search_type: str = "hybrid",
        collection_name: str = settings.MILVUS_MAIN_NAME,
    ) -> List[Dict[str, Any]]:
        """Retrieve raw documents without formatting."""
        if collection_name == settings.MILVUS_SOLIQ_ASSISTANT_NAME:
            return self.search_soliq_assistant(text_query=query, top_k=top_k, collection_name=collection_name)

        if search_type == "sparse":
            return self.search_sparse(text_query=query, top_k=top_k, collection_name=collection_name)
        elif search_type == "dense":
            return self.search_dense(text_query=query, top_k=top_k, collection_name=collection_name)
        elif search_type == "specific":
            return self.search_specific(text_query=query, top_k=top_k, collection_name=collection_name)
        else: # Default to hybrid search
            return self.search_hybrid(
                text_query=query, top_k=top_k, alpha=alpha, collection_name=collection_name)

    async def retrieve_multilingual(
        self,
        query_translations: Dict[str, str],
        top_k: int = settings.TOP_K,
        alpha: float = settings.ALPHA,
        search_type: str = "hybrid",
        collection_name: str = settings.MILVUS_MAIN_NAME,
    ) -> List[Dict[str, Any]]:
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
                    collection_name=collection_name
                )
                all_results.extend(results)
            except Exception as e:
                logger.error(f"[RetrievalService] Search failed for {lang_code}: {e}")
        
        if not all_results:
            return []
            
        # Deduplicate
        unique_results = self._deduplicate_documents(all_results)
        
        # Sort by score descending
        sorted_results = sorted(unique_results, key=lambda x: x.get("score", 0), reverse=True)
        
        # Return top_k
        return sorted_results[:top_k]

    def _deduplicate_documents(self, documents: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
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
    ) -> List[Dict[str, Any]]:
        """Perform sparse vector search using BM25 keyword relevance."""
        return self.db_manager.search_sparse(text_query=text_query, top_k=top_k, collection_name=collection_name)

    def search_dense(
        self,
        text_query: str,
        top_k: int = settings.TOP_K,
        collection_name: str = settings.MILVUS_MAIN_NAME,
    ) -> List[Dict[str, Any]]:
        """Perform dense vector search using semantic embeddings."""
        embedding = self.embedding_manager.embed_query(text_query)
        return self.db_manager.search_dense(embedding, top_k, collection_name)

    def search_soliq_assistant(
        self,
        text_query: str,
        top_k: int = settings.TOP_K,
        collection_name: str = settings.MILVUS_SOLIQ_ASSISTANT_NAME,
    ) -> List[Dict[str, Any]]:
        """Perform soliq assistant search using keyword matching."""
        embedding = self.embedding_manager.embed_query(text_query)
        return self.db_manager.vector_handler.query_soliq_assistant(dense_vector=embedding, top_k=top_k, collection_name=collection_name)

    def search_hybrid(
        self,
        text_query: str,
        top_k: int = settings.TOP_K,
        alpha: float = settings.ALPHA,
        collection_name: str = settings.MILVUS_MAIN_NAME,
    ) -> List[Dict[str, Any]]:
        """Perform hybrid search combining dense vectors and BM25 keyword relevance using Milvus."""
        embedding = self.embedding_manager.embed_query(text_query)
        return self.db_manager.search_hybrid(
            dense_vector=embedding,
            text_query=text_query,
            top_k=top_k,
            alpha=alpha,
            collection_name=collection_name,
        )
    
    def search_specific(
        self,
        text_query: str,
        top_k: int = settings.TOP_K,
        collection_name: str = settings.MILVUS_MAIN_NAME,
    ) -> List[Dict[str, Any]]:
        """Perform specific search when article number asked."""
        return self.db_manager.search_specific(text_query=text_query, top_k=top_k, collection_name=collection_name)

    def search_mongo_fulltext(self, collection: str, text_query: str) -> List[Dict[str, Any]]:
        """Perform regex-based full-text search in a MongoDB collection."""
        query = {"content": {"$regex": text_query, "$options": "i"}}
        return self._format_mongo_results(self.db_manager.find_documents(collection, query))

    def search_mongo_metadata(self, collection: str, filters: Dict[str, Any]) -> List[Dict[str, Any]]:
        """Search MongoDB documents based on metadata fields."""
        return self._format_mongo_results(self.db_manager.find_documents(collection, filters))

    def _format_mongo_results(self, documents: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
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

    async def _format_results(self, documents: List[Dict[str, Any]]) -> str:
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
            if not re.match(r'^\s*#{1,6}\s*', line):  # skip header lines
                cleaned_lines.append(line)
        return "\n".join(cleaned_lines).strip()

        
    async def _build_document_entry(self, metadata: Dict[str, Any]) -> str:
        """Build a formatted entry for a single document."""
        text = metadata.get('text', '')
        url = metadata.get("url")

        is_buxgalter_uz = True if url and 'buxgalter.uz' in url else False
        text = await self.clean_text(text) if is_buxgalter_uz else text
        entry = [f"{'-'*50}", f"Document Content: {self.remove_header_lines(text)}\n"]
        
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
        if document_number := metadata.get("document_number"):
            entry.append(f"Document Number: {document_number}\n")  
            
        return "\n".join(entry)