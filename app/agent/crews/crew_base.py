# app/agent/crews/crew_base.py
from pathlib import Path
from typing import Callable, Dict, Any, List, Optional

import yaml
from crewai import Agent

from app.agent.llm import main_llm, tiny_llm
from app.core.config import settings
from app.chains.prompts import SYSTEM_PROMPT
from app.agent.tools import (
    WebSearchTool,
)


class Agents:
    """Factory for legal-assistant agents."""

    _CONFIG: Dict[str, Dict] = {}

    def __init__(self) -> None:
        if not Agents._CONFIG:
            self._load_config()

    @classmethod
    def _load_config(cls) -> None:
        """Read agents.yaml once and cache at class level."""
        path = Path(__file__).parent / "agents.yaml"
        with path.open(encoding="utf-8") as file:
            data = yaml.safe_load(file)
            cls._CONFIG.update(data.get("agents", {}))

    @staticmethod
    def _apply_customization(
        base: Dict,
        customize: Callable[[Dict], Dict] | None,
    ) -> Dict:
        return customize(base.copy()) if customize else base.copy()

    def _build_agent(
        self,
        name: str,
        llm: Any,
        customize: Callable[[Dict], Dict] | None = None,
        tools: Optional[List[Any]] = None,
        function_calling_llm: Optional[Any] = None,
    ) -> Agent:
        """Create a single Agent from config."""
        config = self._apply_customization(self._CONFIG[name], customize)
        return Agent(
            config=config,
            llm=llm,
            verbose=settings.DEBUG,
            tools=tools or [],
            function_calling_llm=function_calling_llm,
        )

    def memory_summarizer(self) -> Agent:
        # Attach memory tools so the agent can fetch and summarize when invoked
        return self._build_agent(
            "memory_summarizer",
            tiny_llm,
        )

    def retrieval_specialist(self) -> Agent:
        return self._build_agent("retrieval_specialist", tiny_llm)

    def web_search_summarizer(self) -> Agent:
        # Allow tool/function calling for web search
        return self._build_agent(
            "web_search_summarizer",
            tiny_llm,
            tools=[WebSearchTool],
            function_calling_llm=tiny_llm,
        )

    def final_answer(self) -> Agent:
        def inject_prompt(cfg: Dict) -> Dict:
            cfg["goal"] = f"{SYSTEM_PROMPT}\n\nQuery: {{query}}"
            return cfg

        return self._build_agent("final_answer", main_llm, inject_prompt)