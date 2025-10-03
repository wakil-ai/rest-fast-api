# app/llms/base.py
from abc import ABC, abstractmethod
from typing import AsyncGenerator, Tuple, Any, Union
from app.core.config import settings


class LLM(ABC):
    """Abstract base class for different model handlers."""
    
    @abstractmethod
    async def generate_response(
        self, 
        user_prompt: str,
        system_prompt: str,
        stream: bool = settings.STREAM
    ) -> Union[str, AsyncGenerator[str, None]]:
        """
        Generate a response using the specific model.
        
        Returns:
            - str: Complete response if streaming is disabled
            - AsyncGenerator[str, None]: Streaming response if streaming is enabled
        """
        pass 