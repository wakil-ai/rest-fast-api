# app/agent/tools.py

from crewai.tools import BaseTool
from crewai_tools import TavilyExtractorTool, TavilySearchTool
from app.services.memory_service import ChatMemoryService
from app.core.config import settings

chat_memory_service = ChatMemoryService()

class SessionMemoryTool(BaseTool):
    name: str = "get_session_memory"
    description: str = "Retrieve previous conversation memory."

    async def _run(self, user_id: str, session_id: str):
        return await chat_memory_service.get_session_memory(user_id, session_id)

class PersonalMemoryTool(BaseTool):
    name: str = "get_personal_memory"
    description: str = "Retrieve personal memory from mem0."

    async def _run(self, user_id: str):
        return await chat_memory_service.get_all_memories(user_id)
      
        
# Crew AI default tools with config
WebSearchTool = TavilySearchTool(
    api_key=settings.TAVILY_API_KEY,
    search_depth='basic',
    max_results=10,
    include_images=False,
    include_answer=True,
    timeout=10,
)