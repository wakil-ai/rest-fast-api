import asyncio
from typing import AsyncGenerator

from google import genai
from google.genai import types

from app.core.config import settings
from app.llms.base import LLM


class Gemini(LLM):
    def __init__(self, model_name: str = "gemini-3-pro-preview"):
        self.client = genai.Client(api_key=settings.GEMINI_API_KEY)
        self.model = model_name

    async def generate_response(
        self,
        user_prompt: str,
        system_prompt: str,
        stream: bool = settings.STREAM,
    ) -> str | AsyncGenerator[str, None]:
        """
        Generate response using Gemini model.
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
            raise e

    async def _generate_streaming(
        self, system_prompt: str, user_prompt: str
    ) -> AsyncGenerator[str, None]:
        """Generate streaming response (offloaded to thread via queue)."""
        contents = [
            types.Content(
                role="user",
                parts=[
                    types.Part.from_text(text=user_prompt),
                ],
            ),
            types.Content(
                role="system",
                parts=[
                    types.Part.from_text(text=system_prompt),
                ],
            ),
        ]

        generate_content_config = types.GenerateContentConfig(
            thinking_config=types.ThinkingConfig(
                thinking_level="HIGH",
            ),
            temperature=settings.TEMPERATURE,
        )

        queue: asyncio.Queue[str | None] = asyncio.Queue()
        loop = asyncio.get_event_loop()

        def _sync_stream():
            """Run the synchronous Gemini streaming in a thread, pushing chunks to the queue."""
            try:
                for chunk in self.client.models.generate_content_stream(
                    model=self.model,
                    contents=contents,
                    config=generate_content_config,
                ):
                    if chunk.text:
                        loop.call_soon_threadsafe(queue.put_nowait, chunk.text)
            except Exception as e:
                loop.call_soon_threadsafe(queue.put_nowait, e)
            finally:
                loop.call_soon_threadsafe(queue.put_nowait, None)

        # Run the blocking stream in a background thread
        thread_future = loop.run_in_executor(None, _sync_stream)

        # Yield chunks as they arrive from the thread
        while True:
            item = await queue.get()
            if item is None:
                break
            if isinstance(item, Exception):
                raise item
            yield item

        # Ensure the thread has finished
        await thread_future

    async def _generate_complete(self, system_prompt: str, user_prompt: str) -> str:
        """Generate complete response (offloaded to thread)."""
        contents = [
            types.Content(
                role="user",
                parts=[
                    types.Part.from_text(text=user_prompt),
                ],
            ),
            types.Content(
                role="system",
                parts=[
                    types.Part.from_text(text=system_prompt),
                ],
            ),
        ]
        tools = [
            types.Tool(googleSearch=types.GoogleSearch()),
        ]
        generate_content_config = types.GenerateContentConfig(
            thinking_config=types.ThinkingConfig(
                thinking_level="HIGH",
            ),
            tools=tools,
        )

        response = await asyncio.to_thread(
            self.client.models.generate_content,
            model=self.model,
            contents=contents,
            config=generate_content_config,
        )

        return response.text
