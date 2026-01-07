"""Factory for creating and caching legal-assistant agents."""

from collections.abc import Callable
from pathlib import Path
from typing import Any

import yaml
from crewai import Agent

from app.core.config import settings
from app.orchestration.config.llms import tiny_llm
from app.orchestration.tools import WebSearchTool


class Agents:
    """
    Agents are created once on first access and cached for reuse.
    """

    _CONFIG: dict[str, dict] = {}
    _agents_cache: dict[str, Agent] = {}

    def __init__(self) -> None:
        if not Agents._CONFIG:
            self._load_config()

        self._initialize_agents()

    @classmethod
    def _load_config(cls) -> None:
        """Load agent configurations from YAML file."""
        config_path = Path(__file__).parent / "config/agents.yaml"
        with config_path.open(encoding="utf-8") as file:
            data = yaml.safe_load(file)
            cls._CONFIG.update(data.get("agents", {}))

    def _initialize_agents(self) -> None:
        """Create and cache all agents on initialization."""
        if not Agents._agents_cache:
            Agents._agents_cache = {
                "memory_summarizer": self._create_memory_summarizer(),
                "retrieval_specialist": self._create_retrieval_specialist(),
                "context_evaluator": self._create_context_evaluator(),
                "web_search_summarizer": self._create_web_search_summarizer(),
            }

    def _create_agent(
        self,
        name: str,
        llm: Any,
        tools: list[Any] | None = None,
        function_calling_llm: Any | None = None,
        config_modifier: Callable[[dict], dict] | None = None,
    ) -> Agent:
        """
        Create an agent from configuration.

        Args:
            name: Agent configuration key
            llm: Language model for the agent
            tools: Optional list of tools
            function_calling_llm: Optional LLM for function calling
            config_modifier: Optional function to modify config
        """
        config = self._get_agent_config(name, config_modifier)
        if not isinstance(config, dict) or not config:
            raise ValueError(
                f"Invalid agent config for '{name}': expected non-empty dict, got {type(config).__name__}: {config!r}. "
                "Check app/orchestration/config/agents.yaml and any config_modifier functions."
            )

        return Agent(
            config=config,
            llm=llm,
            verbose=settings.DEBUG,
            tools=tools or [],
            function_calling_llm=function_calling_llm,
        )

    def _get_agent_config(
        self,
        name: str,
        config_modifier: Callable[[dict], dict] | None = None,
    ) -> dict:
        """Get agent configuration with optional modifications."""
        base_config = self._CONFIG[name].copy()

        if config_modifier:
            return config_modifier(base_config)

        return base_config

    def _create_memory_summarizer(self) -> Agent:
        """Create agent for summarizing session and personal memory."""
        return self._create_agent(
            name="memory_summarizer",
            llm=tiny_llm,
        )

    def _create_retrieval_specialist(self) -> Agent:
        """Create agent for determining retrieval strategy."""
        return self._create_agent(
            name="retrieval_specialist",
            llm=tiny_llm,
        )

    def _create_context_evaluator(self) -> Agent:
        """Create agent for evaluating context sufficiency."""
        return self._create_agent(
            name="context_evaluator",
            llm=tiny_llm,
        )

    def _create_web_search_summarizer(self) -> Agent:
        """Create agent for web search and content extraction."""
        return self._create_agent(
            name="web_search_summarizer",
            llm=tiny_llm,
            tools=[WebSearchTool],
            function_calling_llm=tiny_llm,
        )

    def memory_summarizer(self) -> Agent:
        """Get cached memory summarizer agent."""
        return self._agents_cache["memory_summarizer"]

    def retrieval_specialist(self) -> Agent:
        """Get cached retrieval specialist agent."""
        return self._agents_cache["retrieval_specialist"]

    def context_evaluator(self) -> Agent:
        """Get cached context evaluator agent."""
        return self._agents_cache["context_evaluator"]

    def web_search_summarizer(self) -> Agent:
        """Get cached web search summarizer agent."""
        return self._agents_cache["web_search_summarizer"]
