from typing import Any

from app.core.config import settings
from app.chains.prompts import (
    PROJECT_FILE_PROMPT,
    PROMPT,
    SOLIQ_PROMPT,
)


class AssistantConfig:
    """Configuration manager for assistants."""

    @classmethod
    def get_assistants(cls) -> dict[str, dict[str, Any]]:
        """Get all assistant configurations."""
        return settings.ASSISTANTS

    @classmethod
    def get_assistant_config(cls, assistant_name: str) -> dict[str, Any]:
        """Get configuration for a specific assistant."""
        assistants = cls.get_assistants()
        if assistant_name not in assistants:
            raise ValueError(f"Assistant '{assistant_name}' not found in configuration")
        return assistants[assistant_name]

    @classmethod
    def get_assistant_names(cls) -> list[str]:
        """Get list of all available assistant names."""
        return list(cls.get_assistants().keys())

    @classmethod
    def is_assistant_available(cls, assistant_name: str) -> bool:
        """Check if an assistant is available."""
        return assistant_name in cls.get_assistants()

    @classmethod
    def get_collection_name(cls, assistant_name: str) -> str:
        """Get collection name for an assistant."""
        config = cls.get_assistant_config(assistant_name)
        return config["collection_name"]

    @classmethod
    def get_credit_cost(cls, assistant_name: str) -> int:
        """Get credit cost for an assistant."""
        config = cls.get_assistant_config(assistant_name)
        return config["credit_cost"]

    @classmethod
    def get_description(cls, assistant_name: str) -> str:
        """Get description for an assistant."""
        config = cls.get_assistant_config(assistant_name)
        return config["description"]

    @classmethod
    def validate_assistant_or_default(cls, assistant_name: str | None) -> str:
        """Validate assistant name and return default if invalid."""
        if assistant_name is None:
            return "main"

        # Try exact match first
        if cls.is_assistant_available(assistant_name):
            return assistant_name

        # Try case-insensitive match
        available_assistants = cls.get_assistant_names()
        for assistant in available_assistants:
            if assistant.lower() == assistant_name.lower():
                return assistant

        # Return default if no match found
        return "main"

    @classmethod
    def get_assistant_prompt_template(cls, assistant_name: str) -> str:
        """Get prompt template for a specific assistant."""
        prompts = {
            "main": PROMPT,
            "umumiy": PROMPT,  # TODO: Need to make 'umumiy' alias to 'main'
            "soliq": SOLIQ_PROMPT,
            "mamuriy_sud": PROMPT,  # TODO:  Add specialized prompt for 'mamuriy_sud' assistant
            "shartnoma": PROMPT,  # TODO:  Add specialized prompt for 'shartnoma' assistant
            "project_file": PROJECT_FILE_PROMPT,
        }
        if assistant_name not in prompts:
            raise ValueError(
                f"Prompt template for assistant '{assistant_name}' not found."
            )
        return prompts[assistant_name]
