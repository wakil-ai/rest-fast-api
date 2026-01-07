"""Crews for the legal assistant."""

from pathlib import Path
from typing import Any

import yaml
from crewai import Agent, Crew, Process, Task

from app.orchestration.agents import Agents


class Crews:
    """
    Crews are created once and cached for reuse.
    """

    _TASKS_CONFIG: dict[str, Any] = {}

    def __init__(self) -> None:
        if not self._TASKS_CONFIG:
            self._load_tasks_config()
        self.agents_factory = Agents()

    @classmethod
    def _load_tasks_config(cls) -> None:
        """Load task configurations from YAML file."""
        config_path = Path(__file__).parent / "config/tasks.yaml"
        with config_path.open(encoding="utf-8") as file:
            data = yaml.safe_load(file)
            cls._TASKS_CONFIG = data.get("tasks", {})

    def _create_task(self, name: str, agent: Agent, **kwargs) -> Task:
        """Create a task from configuration."""
        config = self._TASKS_CONFIG[name].copy()
        # Remove agent name from config as we pass the instance
        if "agent" in config:
            del config["agent"]

        return Task(**config, agent=agent, **kwargs)

    def memory_crew(self) -> Crew:
        """Crew for memory summarization."""
        agent = self.agents_factory.memory_summarizer()
        task = self._create_task("memory_summarization_task", agent)

        return Crew(
            agents=[agent], tasks=[task], process=Process.sequential, verbose=True
        )

    def retrieval_strategy_crew(self) -> Crew:
        """Crew for determining retrieval strategy."""
        agent = self.agents_factory.retrieval_specialist()
        task = self._create_task("retrieval_strategy_task", agent)

        return Crew(
            agents=[agent], tasks=[task], process=Process.sequential, verbose=True
        )

    def evaluation_crew(self) -> Crew:
        """Crew for context evaluation."""
        agent = self.agents_factory.context_evaluator()
        task = self._create_task("context_evaluation_task", agent)

        return Crew(
            agents=[agent], tasks=[task], process=Process.sequential, verbose=True
        )

    def web_search_crew(self) -> Crew:
        """Crew for web search."""
        agent = self.agents_factory.web_search_summarizer()
        task = self._create_task("web_search_task", agent)

        return Crew(
            agents=[agent],
            tasks=[task],
            process=Process.sequential,
            verbose=True,
        )
