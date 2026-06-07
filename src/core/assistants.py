from typing import Any

from app.core.config import settings


class AssistantConfig:
    """Configuration manager for assistants."""

    # Backward-compatible assistant name aliases.
    # Public API can keep using old names while internal code uses canonical ones.
    ASSISTANT_ALIASES: dict[str, str] = {
        "deepresearch": "main",
        "deep_research": "main",
        "court assistant": "court",
        "court": "court",
        "sud": "court",
        "mamuriy_sud": "administrative_court",
        "administrative court": "administrative_court",
        "admin court": "administrative_court",
        "soliq": "tax",
        "tax assistant": "tax",
        "shartnoma": "contract_analyzer",
        "contract analyzer": "contract_analyzer",
    }

    @classmethod
    def get_assistants(cls) -> dict[str, dict[str, Any]]:
        """Get all assistant configurations."""
        return settings.ASSISTANTS

    @classmethod
    def get_public_assistants(cls) -> dict[str, dict[str, Any]]:
        """Get assistants exposed to API clients."""
        return {
            name: config
            for name, config in cls.get_assistants().items()
            if config.get("public", True)
        }

    @classmethod
    def get_assistant_config(cls, assistant_name: str) -> dict[str, Any]:
        """Get configuration for a specific assistant."""
        assistants = cls.get_assistants()

        raw = assistant_name.strip()
        canonical = cls.ASSISTANT_ALIASES.get(raw.lower(), raw)

        if canonical not in assistants:
            raise ValueError(f"Assistant '{assistant_name}' not found in configuration")
        return assistants[canonical]

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
    def is_deep_research_assistant(cls, assistant_name: str | None) -> bool:
        """True when the client explicitly selected the deep-research assistant."""
        raw = (assistant_name or "").strip().lower()
        return raw in {"deepresearch", "deep_research"}

    @classmethod
    def is_web_search_enabled(cls, assistant_name: str | None) -> bool:
        """Web/research behavior is only requested for deep-research turns."""
        return cls.is_deep_research_assistant(assistant_name)

    @classmethod
    def validate_assistant_or_default(cls, assistant_name: str | None) -> str:
        """Validate assistant name and return default if invalid."""
        if assistant_name is None:
            return "main"

        raw = assistant_name.strip()
        canonical = cls.ASSISTANT_ALIASES.get(raw.lower(), raw)

        # Try exact match first
        if cls.is_assistant_available(canonical):
            return canonical

        # Try case-insensitive match
        available_assistants = cls.get_assistant_names()
        for assistant in available_assistants:
            if assistant.lower() == canonical.lower():
                return assistant

        # Return default if no match found
        return "main"
