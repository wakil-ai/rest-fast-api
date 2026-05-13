from pathlib import Path

import yaml

from app.core.dependencies import get_classifier_llm
from app.core.logger import logger
from app.orchestration.prompts import PromptRegistry


class MilvusQueryAgent:
    """Generate Milvus filter expressions for court retrieval."""

    def __init__(self, prompt_registry: PromptRegistry | None = None) -> None:
        self.llm = get_classifier_llm()
        self.prompt_registry = prompt_registry or PromptRegistry()
        self.prompt = self.prompt_registry.get_prompt("milvus_query_agent").template
        self._court_metadata = self._load_court_metadata()

    def _load_court_metadata(self) -> dict:
        config_path = (
            Path(__file__).resolve().parents[2]
            / "assistants"
            / "config"
            / "court_metadata.yaml"
        )
        try:
            with config_path.open(encoding="utf-8") as file:
                data = yaml.safe_load(file) or {}
            agents = data.get("agents")
            return agents if isinstance(agents, dict) else {}
        except Exception as error:
            logger.warning(f"[MilvusQueryAgent] Failed to load court metadata: {error}")
            return {}

    def _format_bullets(self, values: list[str] | None) -> str:
        if not values:
            return "  - (not configured)"
        return "\n".join(f"  - {value}" for value in values if str(value).strip())

    def _get_assistant_lists(self, assistant: str | None) -> tuple[str, str]:
        if not assistant:
            return self._format_bullets(None), self._format_bullets(None)

        cfg = self._court_metadata.get(assistant) or {}
        if not isinstance(cfg, dict):
            cfg = {}

        court_names = cfg.get("court_names_uz")
        categories = cfg.get("categories_uz")
        courts_list = court_names if isinstance(court_names, list) else None
        categories_list = categories if isinstance(categories, list) else None
        return self._format_bullets(courts_list), self._format_bullets(categories_list)

    def _build_prompt(
        self,
        *,
        assistant: str | None,
        query: str,
        chat_history: str,
        file_context: str,
    ) -> str:
        courts_bullets, categories_bullets = self._get_assistant_lists(assistant)
        return self.prompt.format(
            user_query=query,
            file_context=file_context,
            chat_history=chat_history,
            court_names_uz_values=courts_bullets,
            categories_uz_values=categories_bullets,
        )

    async def generate_filter(
        self,
        query: str,
        chat_history: str = "",
        file_context: str = "",
        *,
        assistant: str | None = None,
    ) -> str:
        try:
            structured_prompt = self._build_prompt(
                assistant=assistant,
                query=query,
                chat_history=chat_history,
                file_context=file_context,
            )
            response = await self.llm.generate_response(
                user_prompt=query,
                system_prompt=structured_prompt,
                stream=False,
            )
            logger.debug(f"[MilvusQueryAgent] Generated filter: {response.strip()}")
            return response.strip()
        except Exception as error:
            logger.error(f"[MilvusQueryAgent] Failed to generate filter: {error}")
            return ""
