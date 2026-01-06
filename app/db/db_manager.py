from typing import Any
from app.db.mongo_handler import MongoHandler
from app.db.pinecone_handler import PineconeHandler
from app.db.milvus_handler import MilvusHandler
from app.db.vector_db_handler import VectorDBHandler

from app.core.config import settings, VectorDBType
from app.core.logger import logger


class DBManager:
    """
    Database manager for MongoDB, MySQL, and vector databases (Pinecone, or Milvus).
    """

    # Prevent multiple instances
    _instance = None
    _initialized = False

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super(DBManager, cls).__new__(cls)
        return cls._instance

    def __init__(self):
        if self._initialized:
            return

        self._initialized = True
        self.vector_handler = self._initialize_vector_db()
        self.mongo_handler = MongoHandler()  # For automatic ingestions

    def create_collection(self, collection_name: str) -> None:
        """Create a MongoDB collection if it doesn't exist."""
        # List existing collections first
        existing_collections = self.mongo_handler.db.list_collection_names()

        if collection_name in existing_collections:
            # logger.info(f"[DBManager] Collection {collection_name} already exists in MongoDB.")
            return

        self.mongo_handler.db.create_collection(collection_name)
        logger.info(f"[DBManager] Created MongoDB collection: {collection_name}")

    # Initialize vector database handler
    def _initialize_vector_db(self) -> VectorDBHandler:
        """Create vector database handler based on configuration."""
        if settings.VECTOR_DB_TYPE == "milvus":
            milvus_handler = MilvusHandler()
            logger.info(
                f"[DBManager] Initialized MilvusHandler with collection: {settings.MILVUS_MAIN_NAME}"
            )
            return milvus_handler
        elif settings.VECTOR_DB_TYPE == "pinecone":
            pinecone_handler = PineconeHandler()
            logger.info(
                f"[DBManager] Initialized PineconeHandler with namespace: {settings.NAMESPACE_NAME}"
            )
            return pinecone_handler
        else:
            raise ValueError(
                f"[DBManager] Invalid vector database type: {settings.VECTOR_DB_TYPE}. Please choose from {VectorDBType.values()}"
            )

    # MongoDB operations - synchronous
    def find_documents(
        self, collection_name: str, query: dict[str, Any], limit: int = 50
    ) -> list[dict[str, Any]]:
        """Find documents in MongoDB based on query."""
        return self.mongo_handler.find_documents(collection_name, query, limit)

    def insert_documents(
        self, collection_name: str, documents: list[dict[str, Any]]
    ) -> list:
        """Insert documents into MongoDB collection and return inserted IDs."""
        return self.mongo_handler.insert_documents(collection_name, documents)

    def update_documents(
        self,
        collection_name: str,
        query: dict[str, Any],
        update: dict[str, Any],
        upsert: bool = False,
    ) -> Any:
        """Update documents in MongoDB collection."""
        collection = self.mongo_handler.db[collection_name]
        return collection.update_one(query, update, upsert=upsert)

    def delete_documents(self, collection_name: str, query: dict[str, Any]) -> Any:
        """Delete documents from MongoDB collection."""
        collection = self.mongo_handler.db[collection_name]
        return collection.delete_many(query)

    # Vector database operations (Pinecone, or Milvus)
    def upsert_vectors(
        self, documents: list[dict[str, Any]], partition_name: str = None
    ) -> None:
        """Upsert vectors into vector database."""
        self.vector_handler.upsert_vectors(documents, partition_name)

    def search_dense(
        self,
        dense_vector: list[float],
        top_k: int = settings.TOP_K,
        collection_name: str = settings.MILVUS_MAIN_NAME,
    ) -> list[dict[str, Any]]:
        """Dense-only search."""
        return self.vector_handler.query_dense(
            dense_vector, top_k, collection_name=collection_name
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
    ) -> list[dict[str, Any]]:
        """Hybrid search (dense + sparse)."""
        return self.vector_handler.query_hybrid(
            dense_vector, text_query, top_k, alpha, collection_name=collection_name
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

    def close_all_connections(self):
        """Close all database connections."""
        self.mongo_handler.close_connection()
