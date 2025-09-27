# app/db/mongo_handler.py

from pymongo import MongoClient
from pymongo.errors import ConnectionFailure
from typing import List, Dict, Any
from app.core.config import settings
from bson import ObjectId
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


    def find_documents(self, collection_name: str, query: Dict[str, Any]) -> List[Dict[str, Any]]:
        """
        Find documents matching a query.
        """
        collection = self.db[collection_name]
        cursor = collection.find(query)
        return list(cursor)

    def find_documents_by_urls(self, collection_name: str, urls: List[str]) -> List[Dict[str, Any]]:
        """
        Find documents matching a list of URLs.
        """
        try:
            collection = self.db[collection_name]
            cursor = collection.find({"metadata.url": {"$in": urls}})
            return list(cursor)
        except Exception as e:
            logger.error(f"[MongoHandler] Error finding documents by URLs: {str(e)}")
            return []

    def delete_documents_by_ids(self, collection_name: str, document_ids: List[str]) -> int:
        """
        Delete multiple documents by their _id.
        """
        try:
            collection = self.db[collection_name]
            result = collection.delete_many({"_id": {"$in": [ObjectId(doc_id) for doc_id in document_ids]}})
            logger.info(f"[MongoHandler] Deleted {result.deleted_count} documents.")
            return result.deleted_count
        except Exception as e:
            logger.error(f"[MongoHandler] Error deleting documents by IDs: {str(e)}")
            return 0

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

    def get_all_data(self, collection_name: str = settings.COLLECTION_NAME) -> List[str]:
        """
        Retrieve all documents from a specified collection and return a list of URLs.
        """
        collection = self.db[collection_name]
        cursor = collection.find()
        urls = []
        for document in cursor:
            metadata = document.get("metadata", {})
            url = metadata.get("url")
            if url:
                urls.append(url)
        return urls