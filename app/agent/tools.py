# app/agent/tools.py

from crewai.tools import BaseTool
from crewai_tools import TavilyExtractorTool, TavilySearchTool
from app.db.db_manager import DBManager
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

    async def _run(self, user_id: str, query: str):
        return await chat_memory_service.search_memory(user_id, query)
      
        
# Crew AI default tools with config
WebSearchTool = TavilySearchTool(
    api_key=settings.TAVILY_API_KEY,
    include_images=False,
)

WebExtractionTool = TavilyExtractorTool(
    api_key=settings.TAVILY_API_KEY,
    include_images=False,
)
        
tools = [
    WebSearchTool,
    WebExtractionTool,
    SessionMemoryTool(),
    PersonalMemoryTool(),
]