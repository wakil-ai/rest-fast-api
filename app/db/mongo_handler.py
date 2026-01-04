# app/db/mongo_handler.py

from pymongo import MongoClient
from pymongo.errors import ConnectionFailure
from typing import List, Dict, Any
from app.core.config import settings

from app.core.logger import logger

class MongoHandler:
    """
    MongoDB handler for storing and querying documents.
    """

    def __init__(self):
        try:
            self.client = MongoClient(settings.MONGODB_URI, serverSelectionTimeoutMS=5000, tlsAllowInvalidCertificates=True)
            self.client.admin.command('ping')  # Validate connection
            self.db = self.client[settings.MONGODB_DB_NAME]
            self.collection = self.db[settings.COLLECTION_NAME]
            logger.info("[MongoHandler] Connected to MongoDB")
        except ConnectionFailure as e:
            logger.warning(f"[MongoHandler] Could not connect to MongoDB: {str(e)}")

    def insert_documents(self, collection_name: str, documents: list) -> list:
        """
        Insert multiple documents and return their inserted IDs.
        """
        collection = self.db[collection_name]
        result = collection.insert_many(documents)
        return [str(doc_id) for doc_id in result.inserted_ids]


    def find_documents(self, collection_name: str, query: Dict[str, Any], limit: int = 50) -> List[Dict[str, Any]]:
        """
        Find documents matching a query.
        """
        collection = self.db[collection_name]
        cursor = collection.find(query, limit=limit, sort=[("created_at", -1)])
        return list(cursor)

    def close_connection(self):
        self.client.close
        logger.info("[MongoHandler] MongoDB connection closed.")


    def delete_collection(self, collection_name: str) -> None:
        try:
            self.db.drop_collection(collection_name)
            logger.info(f"[MongoHandler] Dropped collection: {collection_name}")
        except Exception as e:
            logger.error(f"[MongoHandler] Error dropping collection {collection_name}: {str(e)}")


    def clean_collection(self, collection_name: str) -> None:
        """
        Delete all documents from a MongoDB collection without dropping it.
        """
        try:
            result = self.db[collection_name].delete_many({})
            logger.info(f"[MongoHandler] Cleaned {result.deleted_count} documents from '{collection_name}' collection.")
        except Exception as e:
            logger.error(f"[MongoHandler] Error cleaning collection {collection_name}: {str(e)}")
    
    def find_one(self, collection_name: str, query: Dict[str, Any]) -> Dict[str, Any]:
        """
        Find a single document matching the query.
        """
        collection = self.db[collection_name]
        return collection.find_one(query)
    
    def insert_one(self, collection_name: str, document: Dict[str, Any]) -> str:
        """
        Insert a single document and return its ID.
        """
        collection = self.db[collection_name]
        result = collection.insert_one(document)
        return str(result.inserted_id)
    
    def update_one(self, collection_name: str, query: Dict[str, Any], update: Dict[str, Any]) -> bool:
        """
        Update a single document matching the query.
        """
        collection = self.db[collection_name]
        result = collection.update_one(query, {"$set": update})
        return result.modified_count > 0
    
    def find_many(self, collection_name: str, query: Dict[str, Any], limit: int = 100) -> List[Dict[str, Any]]:
        """
        Find multiple documents matching the query.
        """
        collection = self.db[collection_name]
        cursor = collection.find(query).limit(limit)
        return list(cursor)
