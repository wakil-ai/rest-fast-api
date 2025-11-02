import asyncio
import sys

sys.path.append(".")  # Ensure current directory is in path for imports

from app.services.memory_service import ChatMemoryService

chat_memory_service = ChatMemoryService()

async def main():  
    user_id = "5904877504"
    return await chat_memory_service.get_all_memories(user_id)

if __name__ == "__main__":
    memories = asyncio.run(main())
    print(memories)