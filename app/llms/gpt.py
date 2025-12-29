# app/llms/gpt.py
from typing import AsyncGenerator, Union
from openai import AsyncOpenAI
from app.core.config import settings
from app.chains.prompts import PROMPT
from app.llms.base import LLM

from app.core.logger import logger

class ChatGPT(LLM):
    """ChatGPT models using OpenAI API."""
    
    def __init__(self, model_name: str = None):
        self.client = AsyncOpenAI(api_key=settings.OPENAI_API_KEY)
        self.model = model_name if model_name else settings.GPT_COMPLETION_MODEL
    
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
        # Handle parameter change for newer models
        token_param = "max_completion_tokens" if "gpt-5.2" in self.model or self.model.startswith("o1") else "max_tokens"
        kwargs = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": query}
            ],
            "temperature": settings.TEMPERATURE,
            token_param: settings.OUTPUT_MAX_TOKENS,
        }
        
        response = await self.client.chat.completions.create(**kwargs)
        return response.choices[0].message.content.strip()
    
    async def _generate_streaming(self, system_prompt: str, query: str) -> AsyncGenerator[str, None]:
        """Generate streaming response."""
        # Handle parameter change for newer models
        token_param = "max_completion_tokens" if "gpt-5.2" in self.model or self.model.startswith("o1") else "max_tokens"
        kwargs = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": query}
            ],
            "temperature": settings.TEMPERATURE,
            "stream": True,
            token_param: settings.OUTPUT_MAX_TOKENS,
        }
        
        response = await self.client.chat.completions.create(**kwargs)
        
        async for chunk in response:
            content = chunk.choices[0].delta.content
            if content:
                yield content