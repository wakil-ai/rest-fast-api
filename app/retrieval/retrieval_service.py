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

    async def retrieve_context(
        self,
        query: str,
        top_k: int = settings.TOP_K,
        alpha: float = settings.ALPHA,
        collection_name: str = settings.MILVUS_MAIN_NAME,
    ) -> str:
        """Retrieve and format top documents by combining hybrid search and metadata reranking results."""
        # Perform hybrid search using Milvus
        try:
            search_results = self.search_hybrid(
                query_text=query, top_k=top_k, alpha=alpha, collection_name=collection_name
            )
            
            return self._format_results(search_results)
        except Exception as e:
            logger.error(f"[RetrievalService] Retrieval failed: {e}", exc_info=True)
            return "No relevant documents found."

    def search_sparse(
        self,
        query_text: str,
        top_k: int = settings.TOP_K,
    ) -> List[Dict[str, Any]]:
        """Perform sparse vector search using BM25 keyword relevance."""
        return self.db_manager.search_sparse(text_query=query_text, top_k=top_k)

    def search_dense(
        self,
        query_text: str,
        top_k: int = settings.TOP_K,
        collection_name: str = settings.MILVUS_MAIN_NAME,
    ) -> List[Dict[str, Any]]:
        """Perform dense vector search using semantic embeddings."""
        embedding = self.embedding_manager.embed_query(query_text)
        return self.db_manager.search_dense(embedding, top_k, collection_name)

    def search_hybrid(
        self,
        query_text: str,
        top_k: int = settings.TOP_K,
        alpha: float = settings.ALPHA,
        collection_name: str = settings.MILVUS_MAIN_NAME,
    ) -> List[Dict[str, Any]]:
        """Perform hybrid search combining dense vectors and BM25 keyword relevance using Milvus."""
        embedding = self.embedding_manager.embed_query(query_text)
        return self.db_manager.search_hybrid(
            dense_vector=embedding,
            text_query=query_text,
            top_k=top_k,
            alpha=alpha,
            collection_name=collection_name,
        )
    
    def search_specific(
        self,
        query_text: str,
        top_k: int = settings.TOP_K,
    ) -> List[Dict[str, Any]]:
        """Perform specific search when article number asked."""
        return self.db_manager.search_specific(text_query=query_text, top_k=top_k)

    def search_mongo_fulltext(self, collection: str, query_text: str) -> List[Dict[str, Any]]:
        """Perform regex-based full-text search in a MongoDB collection."""
        query = {"content": {"$regex": query_text, "$options": "i"}}
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

    def _format_results(self, documents: List[Dict[str, Any]]) -> str:
        """Format documents into a readable string."""
        formatted_entries = []
        seen_content = set()

        for doc in documents:
            metadata = doc.get("metadata", {})
            entry = self._build_document_entry(
               metadata=metadata,
            )

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

        
    def _build_document_entry(self, metadata: Dict[str, Any]) -> str:
        """Build a formatted entry for a single document."""
        text = metadata.get('text', '')
        entry = [f"{'-'*50}", f"Document Content: {self.remove_header_lines(text)}\n"]
        
        # Add citation 
        if hierarchy := metadata.get("hierarchy_path"):
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
            entry.append(f"Date: {date}\n")
        if document_number := metadata.get("document_number"):
            entry.append(f"Document Number: {document_number}\n")
        if url := metadata.get("chunk_url"):
            entry.append(f"Source URL: {url}\n")
        else:
            if url := metadata.get("url"):
                entry.append(f"Source URL: {url}\n")
            
        return "\n".join(entry)