# app/llms/gpt.py
from typing import AsyncGenerator, Union
from openai import AsyncOpenAI
from app.core.config import settings
from app.chains.prompts import PROMPT
from app.llms.base import LLM

from app.core.logger import logger

class ChatGPT(LLM):
    """ChatGPT models using OpenAI API."""
    
    def __init__(self):
        self.client = AsyncOpenAI(api_key=settings.OPENAI_API_KEY)
        self.model = settings.GPT_COMPLETION_MODEL
    
    async def generate_response(
        self, 
        user_prompt: str,
        system_prompt: str,
        stream: bool = settings.STREAM,
    ) -> Union[str, AsyncGenerator[str, None]]:
        """
        Generate response using GPT model.
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
            logger.error(f"[GPTHandler] Generation Error: {str(e)}")
            raise e
    
    async def _generate_complete(self, system_prompt: str, query: str) -> str:
        """Generate complete response."""
        response = await self.client.chat.completions.create(
            model=self.model,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": query}
            ],
            temperature=settings.TEMPERATURE,
            max_tokens=settings.OUTPUT_MAX_TOKENS,
        )
        
        return response.choices[0].message.content.strip()
    
    async def _generate_streaming(self, system_prompt: str, query: str) -> AsyncGenerator[str, None]:
        """Generate streaming response."""
        response = await self.client.chat.completions.create(
            model=self.model,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": query}
            ],
            temperature=settings.TEMPERATURE,
            stream=True,
            max_tokens=settings.OUTPUT_MAX_TOKENS,
        )
        
        async for chunk in response:
            content = chunk.choices[0].delta.content
            if content:
                yield content