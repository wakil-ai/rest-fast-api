# app/llms/local_vllm.py

from typing import AsyncGenerator, Union
from openai import AsyncOpenAI
from urllib.parse import urljoin
from app.core.config import settings
from app.chains.prompts import SYSTEM_PROMPT_GEMMA
from app.llms.base import LLM
from app.core.logger import logger


class LocalVLLM(LLM):
    """Local vLLM server using OpenAI-compatible API."""

    def __init__(self):
        self.client = AsyncOpenAI(
            base_url=urljoin(settings.LOCAL_VLLM_BASE_URL, "/v1"),   # updated to match curl
            api_key=settings.LOCAL_VLLM_API_KEY              # dummy key, not used
        )
        self.model = settings.LOCAL_VLLM_MODEL
        self.max_tokens = settings.LOCAL_VLLM_MAX_TOKENS

    def _format_prompt(
        self, query: str, context: str, chat_history_text: str, language_instruction: str = None
    ) -> str:
        """Format the complete user prompt."""
        user_prompt = f"""Context:
        {context}

        Chat History:
        {chat_history_text}

        Question: {query}"""
        if language_instruction:
            user_prompt += f"\n\n{language_instruction}"
        return user_prompt

    async def generate_response(
        self,
        query: str,
        context: str,
        chat_history_text: str,
        language_instruction: str = None,
        temperature: float = settings.TEMPERATURE_GEMMA,
    ) -> Union[str, AsyncGenerator[str, None]]:
        """
        Generate response using local vLLM server.
        Returns streaming response if STREAM=True, otherwise complete response.
        """
        try:
            user_prompt = self._format_prompt(query, context, chat_history_text, language_instruction)
            logger.info(f"[LocalVLLMHandler] Generating response with model: {self.model}")

            if settings.STREAM:
                return self._generate_streaming(user_prompt, temperature)
            else:
                return await self._generate_complete(user_prompt, temperature)

        except Exception as e:
            logger.error(f"[LocalVLLMHandler] Generation Error: {str(e)}")
            raise

    async def _generate_streaming(
        self, user_prompt: str, temperature: float = settings.TEMPERATURE_GEMMA
    ) -> AsyncGenerator[str, None]:
        """Generate streaming response."""
        try:
            response = await self.client.chat.completions.create(
                model=self.model,
                stream=True,            # match curl
                max_tokens=self.max_tokens,        # match curl
                temperature=temperature,
                messages=[
                    {"role": "system", "content": SYSTEM_PROMPT_GEMMA},
                    {"role": "user", "content": user_prompt},
                ],
            )

            async for chunk in response:
                if chunk.choices and chunk.choices[0].delta.content:
                    yield chunk.choices[0].delta.content

        except Exception as e:
            logger.error(f"[LocalVLLMHandler] Streaming Error: {str(e)}")
            raise

    async def _generate_complete(
        self, user_prompt: str, temperature: float = settings.TEMPERATURE_GEMMA
    ) -> str:
        """Generate complete response (non-streaming)."""
        try:
            response = await self.client.chat.completions.create(
                model=self.model,
                stream=False,           # match curl
                max_tokens=self.max_tokens,        # match curl
                temperature=temperature,
                messages=[
                    {"role": "system", "content": SYSTEM_PROMPT_GEMMA},
                    {"role": "user", "content": user_prompt},
                ],
            )

            return response.choices[0].message.content

        except Exception as e:
            logger.error(f"[LocalVLLMHandler] Complete Error: {str(e)}")
            raise

