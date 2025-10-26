# app/agent/crew.py

from crewai import Crew, Process
from app.agent.agents import agents
from app.agent.tasks import tasks

crew = Crew(
    agents=agents,
    tasks=tasks,
    process=Process.sequential,  # fixed deterministic control
    verbose=True
)