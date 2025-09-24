# app/llms/local_vllm.py

from typing import AsyncGenerator, Union
from openai import AsyncOpenAI
from urllib.parse import urljoin
from app.core.config import settings
from app.chains.prompts import PROMPT
from app.llms.base import LLM
from app.core.logger import logger


class LocalVLLM(LLM):
    """Local vLLM server using OpenAI-compatible API."""

    def __init__(self):
        self.client = AsyncOpenAI(
            base_url=urljoin(settings.LOCAL_VLLM_BASE_URL, "/v1"),
            api_key=settings.LOCAL_VLLM_API_KEY
        )
        self.model = settings.LOCAL_VLLM_MODEL
        self.max_tokens = settings.OUTPUT_MAX_TOKENS

    async def generate_response(
        self,
        query: str,
        context: str,
        chat_history_text: str,
        language_instruction: str = None,
        stream: bool = settings.STREAM
    ) -> Union[str, AsyncGenerator[str, None]]:
        """
        Generate response using local vLLM server.
        Returns streaming response if STREAM=True, otherwise complete response.
        """
        try:
            system_prompt = PROMPT.format(
            context=context,
            chat_history=chat_history_text,
            language_instruction=language_instruction if language_instruction else ""
            )

            logger.debug(f"[LocalVLLMHandler] Generating response with model: {self.model}")

            if stream:
                return self._generate_streaming(query, system_prompt)
            else:
                return await self._generate_complete(query, system_prompt)

        except Exception as e:
            logger.error(f"[LocalVLLMHandler] Generation Error: {str(e)}")
            raise

    async def _generate_streaming(self, query: str, system_prompt: str) -> AsyncGenerator[str, None]:
        """Generate streaming response."""
        try:
            response = await self.client.chat.completions.create(
                model=self.model,
                stream=True,         
                max_tokens=self.max_tokens,   
                temperature=settings.TEMPERATURE,
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": query},
                ],
            )

            async for chunk in response:
                if chunk.choices and chunk.choices[0].delta.content:
                    yield chunk.choices[0].delta.content

        except Exception as e:
            logger.error(f"[LocalVLLMHandler] Streaming Error: {str(e)}")
            raise

    async def _generate_complete(self, query: str, system_prompt: str) -> str:
        """Generate complete response (non-streaming)."""
        try:
            response = await self.client.chat.completions.create(
                model=self.model,
                stream=False,
                max_tokens=self.max_tokens,
                temperature=settings.TEMPERATURE,
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": query},
                ],
            )

            return response.choices[0].message.content

        except Exception as e:
            logger.error(f"[LocalVLLMHandler] Complete Error: {str(e)}")
            raise

