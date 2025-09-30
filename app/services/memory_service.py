from typing import Any, List
from mem0 import AsyncMemoryClient
from app.core.config import settings
from app.core.logger import logger

class ChatMemoryService:
    def __init__(self):
        self.client = AsyncMemoryClient(api_key=settings.MEM0_API_KEY)

    async def search_memory(self, user_id: str, query: str):
        """Searches for memories for a given user."""
        try:
            logger.debug(f"[ChatMemoryService] Searching memories for user_id: {user_id}")
            filters = {"user_id": user_id}
            results = await self.client.search(query=query, filters=filters, version="v2", output_format="v1.1")
            logger.debug(f"[ChatMemoryService] Found memories: {results}")
            results = results.get("results", [])
            
            return await self._format_memories(results)
            
        except Exception as e:
            logger.error(f"[ChatMemoryService] Could not retrieve memories from mem0: {e}")
            return []

    async def add_memory(self, user_id: str, messages: list):
        """Adds a list of messages to the memory for a given user."""
        try:
            await self.client.add(messages, user_id=user_id, version="v2", output_format="v1.1")
        except Exception as e:
            logger.error(f"Could not add memories to mem0: {e}")

    async def get_memory(self, memory_id: str):
        """Retrieves a single memory by its ID."""
        try:
            logger.debug(f"[ChatMemoryService] Getting memory with id: {memory_id}")
            memory = await self.client.get(memory_id)
            logger.debug(f"[ChatMemoryService] Found memory: {memory}")
            return memory
        except Exception as e:
            logger.error(f"[ChatMemoryService] Could not retrieve memory from mem0: {e}")
            return None

    async def get_all_memories(self, user_id: str):
        """Retrieves all memories for a given user."""
        try:
            logger.debug(f"[ChatMemoryService] Getting all memories for user_id: {user_id}")
            memories = await self.client.get_all(user_id=user_id)
            logger.debug(f"[ChatMemoryService] Found memories: {memories}")
            return memories
        except Exception as e:
            logger.error(f"[ChatMemoryService] Could not retrieve memories from mem0: {e}")
            return []

    async def update_memory(self, memory_id: str, data: dict):
        """Updates a memory with new data."""
        try:
            logger.debug(f"[ChatMemoryService] Updating memory with id: {memory_id}")
            updated_memory = await self.client.update(memory_id=memory_id, data=data)
            logger.debug(f"[ChatMemoryService] Updated memory: {updated_memory}")
            return updated_memory
        except Exception as e:
            logger.error(f"[ChatMemoryService] Could not update memory in mem0: {e}")
            return None

    async def delete_memory(self, memory_id: str):
        """Deletes a single memory by its ID."""
        try:
            logger.debug(f"[ChatMemoryService] Deleting memory with id: {memory_id}")
            await self.client.delete(memory_id=memory_id)
            logger.debug(f"[ChatMemoryService] Deleted memory with id: {memory_id}")
            return True
        except Exception as e:
            logger.error(f"[ChatMemoryService] Could not delete memory from mem0: {e}")
            return False

    async def delete_all_user_memories(self, user_id: str):
        """Deletes all memories for a given user."""
        try:
            logger.debug(f"[ChatMemoryService] Deleting all memories for user_id: {user_id}")
            await self.client.delete_all(user_id=user_id)
            logger.debug(f"[ChatMemoryService] Deleted all memories for user_id: {user_id}")
            return True
        except Exception as e:
            logger.error(f"[ChatMemoryService] Could not delete all user memories from mem0: {e}")
            return False
        
    async def _format_memories(self, memories: List[Any]) -> str:
        """Formats memories from mem0 into a string."""
        if not memories:
            return ""
        
        memories_text = "\n\nRelevant Past Memories:\n"
        for memory in memories:
            if isinstance(memory, dict):
                memories_text += f"- {memory.get('memory', str(memory))}\n"
            else:
                memories_text += f"- {str(memory)}\n"
        
        return memories_text
    
    async def _save_interaction_to_memory(self, user_id: str, query: str, answer: str):
        """Saves the user query and assistant answer to memory."""
        try:
            messages = [
                {"role": "user", "content": query},
                {"role": "assistant", "content": answer},
            ]
            await self.add_memory(user_id, messages)
            logger.debug(f"[ChatService] Saved interaction to memory for user_id: {user_id}")
        except Exception as e:
            logger.error(f"[ChatService] Error saving memory: {e}")