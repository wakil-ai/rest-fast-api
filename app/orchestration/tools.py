# app/orchestration/tools.py

from crewai.tools import BaseTool
from crewai_tools import TavilySearchTool
from app.core.config import settings
        
# Crew AI default tools with config
WebSearchTool = TavilySearchTool(
    api_key=settings.TAVILY_API_KEY,
    search_depth='advanced',
    max_results=10,
    include_images=False,
    include_answer=True,
    timeout=10,
    country='uzbekistan',
    auto_parameters=True,
)