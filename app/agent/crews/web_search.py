# app/agent/web_search.py
"""
Web Search Agent for external legal information retrieval
Uses Tavily API for intelligent web search and content extraction
"""

from crewai import Agent, Task, Crew, Process
from app.agent.tools import WebSearchTool, WebExtractionTool
from app.agent.llm import tiny_llm


WEB_SEARCH_INSTRUCTIONS = """
You are a Web Search Specialist for legal information.

Your job:
1. Use WebSearchTool to find authoritative legal sources
2. Evaluate search results for relevance and authority
3. Return URLs of the most credible sources

Search strategy:
- Prioritize official government websites (.gov, .uz, lex.uz)
- Look for legal databases and law firm resources
- Focus on recent, authoritative content
- Avoid commercial or unreliable sources

Quality criteria:
- Only include URLs that are directly relevant
- Prefer primary sources over secondary
- Maximum 5 URLs to avoid information overload
- Include the search query used for each URL

Never fabricate URLs or sources. If no good results found, return empty list.
"""


WebSearchAgent = Agent(
    role="Web Search Specialist",
    goal=WEB_SEARCH_INSTRUCTIONS,
    backstory=(
        "You are an expert at finding reliable legal information online. "
        "You know how to identify authoritative sources and filter out noise. "
        "You prioritize official legal resources and reputable law databases."
    ),
    llm=tiny_llm,
    function_calling_llm=tiny_llm,
    allow_delegation=False,
    verbose=True,
    tools=[WebSearchTool],
    max_iter=3,
    
)


web_search_task = Task(
    description="""
    Search the web for additional legal information if needed.

    Context provided:
    - Query: {query}

    Your task:
    1. Use WebSearchTool to find authoritative legal sources related to the query.
    2. Only keep URLs from official or reputable legal sources (e.g., lex.uz, gov.uz, reputable law databases).
    3. For each source, extract or summarize the most relevant content in 2–4 sentences.
    4. Return up to 5 high-quality results.

    Return structured JSON data as follows:
    {
        "docs": [
            {
                "content": "extracted legal text or summary, must be human-readable",
                "url": "direct source URL"
            }
        ]
    }
    """,
    expected_output="""
    {
        "docs": [
            {
                "content": "According to the Tax Code of Uzbekistan, fish farming enterprises are exempt from profit tax until 2027 under Article 480.",
                "url": "https://lex.uz/docs/65044"
            },
            {
                "content": "The State Tax Committee clarifies that the 0% corporate tax rate applies to aquaculture companies registered with the Ministry of Agriculture.",
                "url": "https://soliq.uz/page/updates2024"
            },
            {
                "content": "Cabinet of Ministers Resolution No. 43 (2021) outlines tax and customs incentives for enterprises involved in fish processing and export.",
                "url": "https://regulation.gov.uz/uz/document/12345"
            }
        ]
    }
    """,
    agent=WebSearchAgent,
    output_key="web_search_json",
    verbose=True,
)


web_search_crew = Crew(
    agents=[WebSearchAgent],
    tasks=[web_search_task],
    process=Process.sequential,
    verbose=True,
)