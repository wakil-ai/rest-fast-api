from abc import ABC, abstractmethod
from collections.abc import AsyncGenerator
from app.core.config import settings


class LLM(ABC):
    """Abstract base class for different model handlers."""

    @abstractmethod
    async def generate_response(
        self, user_prompt: str, system_prompt: str, stream: bool = settings.STREAM
    ) -> str | AsyncGenerator[str, None]:
        """
        Generate a response using the specific model.

        Returns:
            - str: Complete response if streaming is disabled
            - AsyncGenerator[str, None]: Streaming response if streaming is enabled
        """
        pass
