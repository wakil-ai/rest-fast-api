"""Factory for creating and caching legal-assistant agents."""

from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

import yaml
from crewai import Agent

from app.orchestration.config.llms import main_llm, tiny_llm
from app.orchestration.tools import WebSearchTool
from app.chains.prompts import SYSTEM_PROMPT
from app.core.config import settings


class Agents:
    """
    Agents are created once on first access and cached for reuse.
    """

    _CONFIG: Dict[str, Dict] = {}
    _agents_cache: Dict[str, Agent] = {}

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
                "final_answer": self._create_final_answer(),
            }

    def _create_agent(
        self,
        name: str,
        llm: Any,
        tools: Optional[List[Any]] = None,
        function_calling_llm: Optional[Any] = None,
        config_modifier: Optional[Callable[[Dict], Dict]] = None,
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
        config_modifier: Optional[Callable[[Dict], Dict]] = None,
    ) -> Dict:
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

    def _create_final_answer(self) -> Agent:
        """Create agent for generating final answers."""
        return self._create_agent(
            name="final_answer",
            llm=main_llm,
            config_modifier=self._inject_system_prompt,
        )

    @staticmethod
    def _inject_system_prompt(config: Dict) -> Dict:
        """Inject system prompt into agent goal."""
        config["goal"] = f"{SYSTEM_PROMPT}\n\nQuery: {{query}}"
        return config

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

    def final_answer(self) -> Agent:
        """Get cached final answer agent."""
        return self._agents_cache["final_answer"]