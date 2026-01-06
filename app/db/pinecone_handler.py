from typing import Any
from app.core.config import settings
from pinecone import Pinecone as OfficialPinecone
from app.core.logger import logger
from app.db.vector_db_handler import VectorDBHandler


class PineconeHandler(VectorDBHandler):
    """
    Handler for inserting and querying vectors into Pinecone,
    supporting dense, BM25, and hybrid search modes.
    """

    def __init__(self):
        self.pc = OfficialPinecone(api_key=settings.PINECONE_API_KEY)
        self.index = self.pc.Index(settings.PINECONE_INDEX_NAME)
        self.namespace = settings.NAMESPACE_NAME

    def upsert_vectors(
        self, documents: list[dict[str, Any]], partition_name: str = None
    ) -> None:
        vectors = []

        for doc in documents:
            dense_embedding = doc.get("embedding")
            if not dense_embedding:
                logger.warning(
                    f"[PineconeHandler] Missing dense embedding for document ID: {doc.get('id')}"
                )
                continue

            metadata = doc.get("metadata", {})
            if "text" not in metadata and doc.get("text"):
                metadata["text"] = doc["text"]

            vector_entry = {
                "id": doc["id"],
                "values": dense_embedding,
                "metadata": metadata,
            }
            vectors.append(vector_entry)

        if not vectors:
            logger.warning("[PineconeHandler] No vectors to upsert.")
            return

        self.index.upsert(vectors=vectors, namespace=self.namespace)
        logger.info(
            f"[PineconeHandler] Upserted {len(vectors)} vectors into namespace '{self.namespace}'."
        )

    def query_dense(
        self, dense_vector: list[float], top_k: int = settings.TOP_K
    ) -> list[dict[str, Any]]:
        """Dense-only search."""
        response = self.index.query(
            namespace=self.namespace,
            vector=dense_vector,
            top_k=top_k,
            include_metadata=True,
            include_values=False,
        )
        return self._parse_matches(response)

    def query_sparse(
        self, text_query: str, top_k: int = settings.TOP_K
    ) -> list[dict[str, Any]]:
        """BM25-only text search."""
        response = self.index.query(
            namespace=self.namespace,
            text=text_query,
            top_k=top_k,
            include_metadata=True,
            include_values=False,
        )
        return self._parse_matches(response)

    def query_hybrid(
        self,
        dense_vector: list[float],
        text_query: str,
        top_k: int = settings.TOP_K,
        alpha: float = settings.ALPHA,
    ) -> list[dict[str, Any]]:
        """Hybrid search combining dense and BM25."""
        response = self.index.query(
            namespace=self.namespace,
            vector=dense_vector,
            text=text_query,
            hybrid=True,
            alpha=alpha,
            top_k=top_k,
            include_metadata=True,
            include_values=False,
        )
        return self._parse_matches(response)

    def direct_query(self, query_params: dict[str, Any]) -> list[dict[str, Any]]:
        """
        Perform a direct custom query on Pinecone index.
        Useful for fallback debugging.
        """
        response = self.index.query(**query_params)
        return self._parse_matches(response)

    def delete_namespace(self) -> None:
        try:
            self.index.delete(delete_all=True, namespace=self.namespace)
            logger.info(f"[PineconeHandler] Deleted namespace: {self.namespace}")
        except Exception as e:
            logger.error(
                f"[PineconeHandler] Error deleting namespace {self.namespace}: {str(e)}"
            )

    def _parse_matches(self, response: dict[str, Any]) -> list[dict[str, Any]]:
        matches = response.get("matches", [])
        results = []
        for match in matches:
            results.append(
                {
                    "id": match.get("id"),
                    "score": match.get("score"),
                    "metadata": match.get("metadata", {}),
                }
            )
        return results
