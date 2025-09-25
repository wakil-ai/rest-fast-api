# app/llms/novita.py

from typing import AsyncGenerator, Union
from openai import AsyncOpenAI
from app.core.config import settings
from app.chains.prompts import PROMPT
from app.llms.base import LLM

from app.core.logger import logger

class Novita(LLM):
    """Novita AI models using OpenAI-compatible API."""
    
    def __init__(self):
        self.client = AsyncOpenAI(
            base_url="https://api.novita.ai/v3/openai",
            api_key=settings.NOVITA_API_KEY
        )
        self.model = settings.NOVITA_MODEL
    
    async def generate_response(
        self, 
        query: str, 
        context: str, 
        chat_history_text: str,
        language_instruction: str = None,
        stream: bool = settings.STREAM
    ) -> Union[str, AsyncGenerator[str, None]]:
        """
        Generate response using Novita AI model.
        Returns streaming response if STREAM=True, otherwise complete response.
        """
        try:
            system_prompt = PROMPT.format(
                context=context,
                chat_history=chat_history_text,
                language_instruction=language_instruction
            )
            
            if stream:
                # Return streaming response
                return self._generate_streaming(system_prompt, query)
            else:
                # Return complete response
                return await self._generate_complete(system_prompt, query)
                
        except Exception as e:
            logger.error(f"[NovitaHandler] Generation Error: {str(e)}")
            raise e
    
    async def _generate_streaming(self, system_prompt: str, query: str, temperature: float = settings.TEMPERATURE) -> AsyncGenerator[str, None]:
        """Generate streaming response."""
        try:
            response = await self.client.chat.completions.create(
                model=self.model,
                messages=[
                    {
                        "role": "system",
                        "content": system_prompt
                    },
                    {
                        "role": "user",
                        "content": query
                    }
                ],
                stream=True,
                max_tokens=settings.OUTPUT_MAX_TOKENS,
                temperature=temperature
            )
            
            async for chunk in response:
                if chunk.choices and len(chunk.choices) > 0 and chunk.choices[0].delta.content:
                    yield chunk.choices[0].delta.content
                    
        except Exception as e:
            logger.error(f"[NovitaHandler] Streaming Error: {str(e)}")
            raise e
    
    async def _generate_complete(self, system_prompt: str, query: str, temperature: float = settings.TEMPERATURE) -> str:
        """Generate complete response."""
        try:
            response = await self.client.chat.completions.create(
                model=self.model,
                messages=[
                    {
                        "role": "system",
                        "content": system_prompt
                    },
                    {
                        "role": "user",
                        "content": query
                    }
                ],
                stream=False,
                max_tokens=settings.OUTPUT_MAX_TOKENS,
                temperature=temperature
            )
            
            return response.choices[0].message.content
            
        except Exception as e:
            logger.error(f"[NovitaHandler] Complete Error: {str(e)}")
            raise e 