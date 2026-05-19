from typing import Any

from app.core.config import VectorDBType, settings
from app.core.dependencies import (
    get_milvus_handler,
    get_mongo_handler,
    get_pinecone_handler,
)
from app.core.logger import logger


class DBManager:
    """
    Database manager for MongoDB, MySQL, and vector databases (Pinecone, or Milvus).
    """

    def __init__(self):
        self.vector_handler = self._initialize_vector_db()
        self.mongo_handler = get_mongo_handler()

    async def create_collection(self, collection_name: str) -> None:
        """Create a MongoDB collection if it doesn't exist."""
        # List existing collections first
        existing_collections = await self.mongo_handler.db.list_collection_names()

        if collection_name in existing_collections:
            # logger.info(f"[DBManager] Collection {collection_name} already exists in MongoDB.")
            return

        await self.mongo_handler.db.create_collection(collection_name)

    # Initialize vector database handler
    def _initialize_vector_db(self):
        """Create vector database handler based on configuration."""
        if settings.VECTOR_DB_TYPE == "milvus":
            handler = get_milvus_handler()
            logger.info(
                f"[DBManager] Initialized MilvusHandler with collection: {settings.MILVUS_URI}"
            )
            return handler
        elif settings.VECTOR_DB_TYPE == "pinecone":
            handler = get_pinecone_handler()
            logger.info(
                f"[DBManager] Initialized PineconeHandler with namespace: {settings.NAMESPACE_NAME}"
            )
            return handler
        else:
            raise ValueError(
                f"[DBManager] Invalid vector database type: {settings.VECTOR_DB_TYPE}. Please choose from {VectorDBType.values()}"
            )

    # MongoDB operations - asynchronous
    async def find_documents(
        self,
        collection_name: str,
        query: dict[str, Any],
        limit: int = 50,
        skip: int = 0,
    ) -> list[dict[str, Any]]:
        """Find documents in MongoDB based on query."""
        return await self.mongo_handler.find_documents(
            collection_name, query, limit, skip
        )

    async def insert_documents(
        self, collection_name: str, documents: list[dict[str, Any]]
    ) -> list:
        """Insert documents into MongoDB collection and return inserted IDs."""
        return await self.mongo_handler.insert_documents(collection_name, documents)

    async def update_documents(
        self,
        collection_name: str,
        query: dict[str, Any],
        update: dict[str, Any],
        upsert: bool = False,
    ) -> Any:
        """Update documents in MongoDB collection."""
        collection = self.mongo_handler.db[collection_name]
        result = await collection.update_one(query, update, upsert=upsert)
        return result.modified_count

    async def delete_documents(
        self, collection_name: str, query: dict[str, Any]
    ) -> Any:
        """Delete documents from MongoDB collection."""
        collection = self.mongo_handler.db[collection_name]
        result = await collection.delete_many(query)
        return {"deleted_count": result.deleted_count}

    def search_dense(
        self,
        dense_vector: list[float],
        top_k: int = settings.TOP_K,
        collection_name: str = settings.MILVUS_MAIN_NAME,
        expr: str = None,
    ) -> list[dict[str, Any]]:
        """Dense-only search."""
        return self.vector_handler.query_dense(
            dense_vector, top_k, collection_name=collection_name, expr=expr
        )

    def search_sparse(
        self,
        text_query: str,
        top_k: int = settings.TOP_K,
        collection_name: str = settings.MILVUS_MAIN_NAME,
    ) -> list[dict[str, Any]]:
        """Sparse-only search."""
        return self.vector_handler.query_sparse(
            text_query, top_k, collection_name=collection_name
        )

    def search_hybrid(
        self,
        dense_vector: list[float],
        text_query: str,
        top_k: int = settings.TOP_K,
        alpha: float = settings.ALPHA,
        collection_name: str = settings.MILVUS_MAIN_NAME,
        expr: str = None,
    ) -> list[dict[str, Any]]:
        """
        Hybrid search (dense + sparse).

        When a filter is provided: run hybrid with that filter first; on any error
        or empty hits, fall back to dense vector search with no filter.
        """
        from app.utils.milvus_expr import normalize_milvus_expr

        filter_expr = normalize_milvus_expr(expr)
        if not filter_expr:
            return self.vector_handler.query_hybrid(
                dense_vector,
                text_query,
                top_k,
                alpha,
                collection_name=collection_name,
                expr=None,
            )

        try:
            results = self.vector_handler.query_hybrid(
                dense_vector,
                text_query,
                top_k,
                alpha,
                collection_name=collection_name,
                expr=filter_expr,
            )
        except Exception as exc:
            logger.warning(
                f"Hybrid search with filter failed; falling back to dense search without filter. collection={collection_name} filter={filter_expr} error={exc}"
            )
            return self.search_dense(
                dense_vector,
                top_k,
                collection_name=collection_name,
                expr=None,
            )

        if results:
            return results

        logger.warning(
            f"Hybrid search with filter returned no results; falling back to dense search without filter. collection={collection_name} filter={filter_expr}"
        )
        return self.search_dense(
            dense_vector,
            top_k,
            collection_name=collection_name,
            expr=None,
        )

    def search_specific(
        self,
        text_query: str,
        top_k: int = settings.TOP_K,
        collection_name: str = settings.MILVUS_MAIN_NAME,
    ) -> list[dict[str, Any]]:
        """Specific search when article number asked."""
        if VectorDBType.milvus == settings.VECTOR_DB_TYPE:
            return self.vector_handler.query_specific(
                text_query, top_k, collection_name=collection_name
            )
        else:
            # Return empty list
            return []

    def _upsert_vectors(
        self,
        documents: list[dict[str, Any]],
        collection_name: str = settings.MILVUS_MAIN_NAME,
        partition_name: str = None,
    ) -> None:
        """Upsert vectors into vector database."""
        self.vector_handler.upsert_vectors(
            documents=documents,
            collection_name=collection_name,
            partition_name=partition_name,
        )

    def delete_vectors_by_filter(
        self,
        filter_expr: str,
        collection_name: str = settings.MILVUS_MAIN_NAME,
    ) -> int:
        """Delete vector rows matching a Milvus boolean filter expression."""
        if settings.VECTOR_DB_TYPE != "milvus":
            return 0

        delete_fn = getattr(self.vector_handler, "delete_vectors_by_filter", None)
        if delete_fn is None:
            logger.warning(
                f"Vector handler does not support delete_vectors_by_filter "
                f"for collection {collection_name}"
            )
            return 0

        return delete_fn(filter_expr, collection_name=collection_name)

    def close_all_connections(self):
        """Close all database connections."""
        self.mongo_handler.close_connection()
