from collections.abc import AsyncGenerator
from anthropic import AsyncAnthropic
from app.core.config import settings
from app.llms.base import LLM
from app.core.logger import logger


class Claude(LLM):
    """Claude models using Anthropic API."""

    def __init__(self, model_name: str = "claude-opus-4-5-20251101"):
        self.client = AsyncAnthropic(api_key=settings.ANTHROPIC_API_KEY)
        self.model = model_name

    async def generate_response(
        self,
        user_prompt: str,
        system_prompt: str,
        stream: bool = settings.STREAM,
    ) -> str | AsyncGenerator[str, None]:
        """
        Generate response using Claude model.
        Returns streaming response if STREAM=True, otherwise complete response.
        """
        try:
            if stream:
                # Return streaming response
                return self._generate_streaming(system_prompt, user_prompt)
            else:
                # Return complete response
                return await self._generate_complete(system_prompt, user_prompt)

        except Exception as e:
            logger.error(f"[ClaudeHandler] Generation Error: {str(e)}")
            raise e

    async def _generate_complete(self, system_prompt: str, query: str) -> str:
        """Generate complete response."""
        response = await self.client.messages.create(
            model=self.model,
            max_tokens=settings.OUTPUT_MAX_TOKENS,
            temperature=settings.TEMPERATURE,
            system=system_prompt,
            messages=[{"role": "user", "content": query}],
        )

        return response.content[0].text.strip()

    async def _generate_streaming(
        self, system_prompt: str, query: str
    ) -> AsyncGenerator[str, None]:
        """Generate streaming response."""
        async with self.client.messages.stream(
            model=self.model,
            max_tokens=settings.OUTPUT_MAX_TOKENS,
            temperature=settings.TEMPERATURE,
            system=system_prompt,
            messages=[{"role": "user", "content": query}],
        ) as stream:
            async for text in stream.text_stream:
                if text:
                    yield text
