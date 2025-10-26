# app/agent/crew.py

from crewai import Crew, Process
from app.agent.agents import (
    CoordinatorAgent, RetrievalAgent, MemoryAgent, WebExtractorAgent,
    WebSearchAgent, FinalAnswerAgent
)
from app.agent.tasks import (
    CoordinatorTask, RetrievalTask, MemoryTask, WebExtractorTask,
    WebSearchTask, FinalAnswerTask
)

crew = Crew(
    agents=[
        CoordinatorAgent,
        RetrievalAgent,
        MemoryAgent,
        WebSearchAgent,
        FinalAnswerAgent,
        WebExtractorAgent
    ],
    tasks=[
        WebExtractorTask,
        CoordinatorTask,
        RetrievalTask,
        MemoryTask,
        WebSearchTask,
        FinalAnswerTask
    ],
    process=Process.sequential,  # fixed deterministic control
    verbose=True
)