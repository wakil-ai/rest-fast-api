# app/db/db_manager.py

from typing import List, Dict, Any
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
        self.mongo_handler = MongoHandler() # For automatic ingestions
    
    # Initialize vector database handler
    def _initialize_vector_db(self) -> VectorDBHandler:
        """Create vector database handler based on configuration."""
        if settings.VECTOR_DB_TYPE == "milvus":
            milvus_handler = MilvusHandler()
            logger.info(f"[DBManager] Initialized MilvusHandler with collection: {settings.MILVUS_COLLECTION_NAME}")
            return milvus_handler
        elif settings.VECTOR_DB_TYPE == "pinecone": 
            pinecone_handler = PineconeHandler()
            logger.info(f"[DBManager] Initialized PineconeHandler with namespace: {settings.NAMESPACE_NAME}")
            return pinecone_handler
        else:
            raise ValueError(f"[DBManager] Invalid vector database type: {settings.VECTOR_DB_TYPE}. Please choose from {VectorDBType.values()}")
        
    def get_all_data(self, collection_name: str = settings.COLLECTION_NAME) -> str:
        """
        Retrieve all data from MongoDB collection.
        
        Args:
            collection_name (str): Name of the MongoDB collection. Defaults to settings.COLLECTION_NAME.
        
        Returns:
            List of documents with metadata.
        """
        return self.mongo_handler.get_all_data(collection_name)
    
    # MongoDB operations - synchronous
    def find_documents(self, collection_name: str, query: Dict[str, Any]) -> List[Dict[str, Any]]:
        """Find documents in MongoDB based on query."""
        return self.mongo_handler.find_documents(collection_name, query)

    def insert_documents(self, collection_name: str, documents: List[Dict[str, Any]]) -> list:
        """Insert documents into MongoDB collection and return inserted IDs."""
        return self.mongo_handler.insert_documents(collection_name, documents)

    def update_documents(self, collection_name: str, query: Dict[str, Any], update: Dict[str, Any], upsert: bool = False) -> Any:
        """Update documents in MongoDB collection."""
        collection = self.mongo_handler.db[collection_name]
        return collection.update_one(query, update, upsert=upsert)

    # Vector database operations (Pinecone, or Milvus)
    def upsert_vectors(self, documents: List[Dict[str, Any]], partition_name: str = None) -> None:
        """Upsert vectors into vector database."""
        self.vector_handler.upsert_vectors(documents, partition_name)

    def search_dense(self, dense_vector: List[float], top_k: int = settings.TOP_K) -> List[Dict[str, Any]]:
        """Dense-only search."""
        return self.vector_handler.query_dense(dense_vector, top_k)

    def search_sparse(self, text_query: str, top_k: int = settings.TOP_K) -> List[Dict[str, Any]]:
        """Sparse-only search."""
        return self.vector_handler.query_sparse(text_query, top_k)

    def search_hybrid(self, dense_vector: List[float], text_query: str, top_k: int = settings.TOP_K, alpha: float = settings.ALPHA) -> List[Dict[str, Any]]:
        """Hybrid search (dense + sparse)."""
        return self.vector_handler.query_hybrid(dense_vector, text_query, top_k, alpha)

    def search_specific(self, text_query: str, top_k: int = settings.TOP_K) -> List[Dict[str, Any]]:
        """Specific search when article number asked."""
        if settings.VECTOR_DB_TYPE == VectorDBType.milvus:
            return self.vector_handler.query_specific(text_query, top_k)
        else:
            # Return empty list
            return []

    def delete_data_by_urls(self, urls: List[str]) -> Dict[str, int]:
        """
        Delete data from MongoDB and the vector database by a list of URLs.
        """
        # Find documents in MongoDB by URLs
        documents = self.mongo_handler.find_documents_by_urls(settings.COLLECTION_NAME, urls)
        if not documents:
            return {"mongo_deleted_count": 0, "vector_db_deleted_count": 0}

        # Get the IDs of the documents
        document_ids = [str(doc["_id"]) for doc in documents]

        # Delete documents from MongoDB
        mongo_deleted_count = self.mongo_handler.delete_documents_by_ids(settings.COLLECTION_NAME, document_ids)

        # Delete vectors from the vector database
        try:
            if settings.VECTOR_DB_TYPE == VectorDBType.milvus:
                self.vector_handler.delete_vectors(document_ids)
            # Add logic for other vector databases if needed
        except Exception as e:
            logger.error(f"[DBManager] Error deleting vectors: {str(e)}")
            # If vector DB deletion fails, we should ideally handle this case.
            # For now, we'll just log the error and return the count of deleted mongo documents.
            return {"mongo_deleted_count": mongo_deleted_count, "vector_db_deleted_count": 0}

        return {"mongo_deleted_count": mongo_deleted_count, "vector_db_deleted_count": len(document_ids)}

    def close_all_connections(self):
        """Close all database connections."""
        self.mysql_handler.close()
        self.mongo_handler.close_connection() 
