# app/agent/tasks.py

from crewai import Task
from app.agent.agents import (
    CoordinatorAgent,
    RetrievalAgent,
    MemoryAgent,
    WebSearchAgent,
    WebExtractorAgent,
    FinalAnswerAgent,
)
from app.agent.tools import (
    RetrievalTool,
    SessionMemoryTool,
    PersonalMemoryTool,
    search_tool, extract_tool,
)

CoordinatorTask = Task(
    description="Understand the user's question and decide retrieval approach.",
    expected_output="A planning summary indicating which agents should be used.",
    agent=CoordinatorAgent,
)

RetrievalTask = Task(
    description="Rewrite the query, decide retrieval type, and retrieve documents.",
    expected_output="Relevant document excerpts.",
    agent=RetrievalAgent,
    tools=[RetrievalTool()],
)

MemoryTask = Task(
    description="Determine if session/personal memory is needed and retrieve it.",
    expected_output="Relevant session/personal memory data.",
    agent=MemoryAgent,
    tools=[SessionMemoryTool(), PersonalMemoryTool()],
)

WebSearchTask = Task(
    description="""
    Search the web for the most relevant legal sources based on the query below.
    OUTPUT SHOULD BE A LIST OF URLs.
    
    LINKS must not be same or duplicated.
    
    Query:
    {query}
    """,
    expected_output="A list of the most relevant URLs (preferably lex.uz).",
    agent=WebSearchAgent,
    tools=[search_tool],
)

WebExtractorTask = Task(
    description="""
    Given these URLs from previous task, extract the **legal content** cleanly and concisely.

    Make sure:
    - Remove ads, UI text, and irrelevant blocks.
    - Preserve article numbering and clause structure.
    - Do not hallucinate, return only what is asked in the query.
    - Do not summarize or interpret, just extract the legal text as is.
    
    Query is:
    {query}
    """,
    expected_output="A structured JSON format containing extracted legal text with usefull metadata if exists.",
    agent=WebExtractorAgent,
    tools=[extract_tool],
)

FinalAnswerTask = Task(
    description="Combine context and generate final legal answer.",
    expected_output="Final structured answer message.",
    agent=FinalAnswerAgent,
)
