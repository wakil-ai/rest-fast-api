from typing import Any

from motor.motor_asyncio import AsyncIOMotorClient
from pymongo.server_api import ServerApi

from app.core.config import settings
from app.core.logger import logger


class MongoHandler:
    """
    Async MongoDB handler using Motor for storing and querying documents.

    Note: Instance sharing is now managed by FastAPI dependency injection.
    Use get_mongo_handler() from app.core.dependencies instead of direct instantiation.
    """

    def __init__(self):
        """Initialize async MongoDB connection using Motor."""
        try:
            self.client = AsyncIOMotorClient(
                settings.MONGODB_URI,
                server_api=ServerApi("1"),
                serverSelectionTimeoutMS=5000,
                tlsAllowInvalidCertificates=True,
            )
            self.db = self.client[settings.MONGODB_DB_NAME]
            self.collection = self.db[settings.COLLECTION_NAME]
            logger.info("[MongoHandler] Initialized async MongoDB connection")
        except Exception as e:
            logger.warning(f"[MongoHandler] Could not initialize MongoDB: {str(e)}")

    async def ping_server(self) -> bool:
        """Check if MongoDB server is reachable (async)."""
        try:
            await self.client.admin.command("ping")
            logger.info("[MongoHandler] MongoDB ping successful")
            return True
        except Exception as e:
            logger.warning(f"[MongoHandler] MongoDB ping failed: {str(e)}")
            return False

    async def insert_documents(self, collection_name: str, documents: list) -> list:
        """
        Insert multiple documents and return their inserted IDs.
        """
        collection = self.db[collection_name]
        result = await collection.insert_many(documents)
        return [str(doc_id) for doc_id in result.inserted_ids]

    async def find_documents(
        self, collection_name: str, query: dict[str, Any], limit: int = 50
    ) -> list[dict[str, Any]]:
        """
        Find documents matching a query.
        """
        collection = self.db[collection_name]
        cursor = collection.find(query).sort("created_at", -1).limit(limit)
        return await cursor.to_list(length=limit)

    async def close_connection(self):
        self.client.close()
        logger.info("[MongoHandler] MongoDB connection closed.")

    async def delete_collection(self, collection_name: str) -> None:
        """Drop a collection from the database."""
        try:
            await self.db.drop_collection(collection_name)
            logger.info(f"[MongoHandler] Dropped collection: {collection_name}")
        except Exception as e:
            logger.error(
                f"[MongoHandler] Error dropping collection {collection_name}: {str(e)}"
            )

    async def clean_collection(self, collection_name: str) -> None:
        """
        Delete all documents from a MongoDB collection without dropping it.
        """
        try:
            result = await self.db[collection_name].delete_many({})
            logger.info(
                f"[MongoHandler] Cleaned {result.deleted_count} documents from '{collection_name}' collection."
            )
        except Exception as e:
            logger.error(
                f"[MongoHandler] Error cleaning collection {collection_name}: {str(e)}"
            )

    async def find_one(
        self, collection_name: str, query: dict[str, Any]
    ) -> dict[str, Any]:
        """
        Find a single document matching the query.
        """
        collection = self.db[collection_name]
        return await collection.find_one(query)

    async def insert_one(self, collection_name: str, document: dict[str, Any]) -> str:
        """
        Insert a single document and return its ID.
        """
        collection = self.db[collection_name]
        result = await collection.insert_one(document)
        return str(result.inserted_id)

    async def update_one(
        self, collection_name: str, query: dict[str, Any], update: dict[str, Any]
    ) -> bool:
        """
        Update a single document matching the query.
        """
        collection = self.db[collection_name]
        result = await collection.update_one(query, {"$set": update})
        return result.modified_count > 0

    async def find_many(
        self, collection_name: str, query: dict[str, Any], limit: int = 100
    ) -> list[dict[str, Any]]:
        """
        Find multiple documents matching the query.
        """
        collection = self.db[collection_name]
        cursor = collection.find(query).limit(limit)
        return await cursor.to_list(length=limit)
