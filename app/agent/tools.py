# app/agent/tools.py

from crewai.tools import BaseTool
from crewai_tools import TavilyExtractorTool, TavilySearchTool
from app.retrieval.retrieval_service import RetrievalService
from app.services.memory_service import ChatMemoryService
from app.core.config import settings

# Import class
retreival_service = RetrievalService()
chat_memory_service = ChatMemoryService()

# Crew AI default tools with config
search_tool = TavilySearchTool(
    api_key=settings.TAVILY_API_KEY,
    include_images=False,
)

extract_tool = TavilyExtractorTool(
    api_key=settings.TAVILY_API_KEY,
    include_images=False,
)

class RetrievalTool(BaseTool):
    name: str = "retrieve_documents"
    description: str = "Retrieve relevant legal documents using an appropriate search strategy."

    async def _run(self, rewritten_query: str, retrieval_type: str):
        return await retreival_service.retrieve_context(query=rewritten_query, search_type=retrieval_type)

class SessionMemoryTool(BaseTool):
    name: str = "get_session_memory"
    description: str = "Retrieve previous conversation memory."

    async def _run(self, user_id: str, session_id: str):
        return await retreival_service.get_session_memory(user_id, session_id)

class PersonalMemoryTool(BaseTool):
    name: str = "get_personal_memory"
    description: str = "Retrieve personal memory from mem0."

    async def _run(self, user_id: str, query: str):
        return await chat_memory_service.search_memory(user_id, query)