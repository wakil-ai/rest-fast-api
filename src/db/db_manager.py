from typing import Any

from core.dependencies import get_mongo_handler


class DBManager:
    """MongoDB manager for the public/core backend."""

    def __init__(self):
        self.mongo_handler = get_mongo_handler()

    async def create_collection(self, collection_name: str) -> None:
        """Create a MongoDB collection if it doesn't exist."""
        # List existing collections first
        existing_collections = await self.mongo_handler.db.list_collection_names()

        if collection_name in existing_collections:
            # logger.info(f"[DBManager] Collection {collection_name} already exists in MongoDB.")
            return

        await self.mongo_handler.db.create_collection(collection_name)

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


    def close_all_connections(self):
        """Close all database connections."""
        self.mongo_handler.close_connection()
