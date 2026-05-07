import asyncio
from typing import Any, AsyncGenerator

from google import genai
from google.genai import types

from app.core.config import settings
from app.llms.base import LLM


class Gemini(LLM):
    def __init__(self, model_name: str = "gemini-3.1-pro-preview"):
        self.client = genai.Client(api_key=settings.GEMINI_API_KEY)
        self.model = model_name

    async def count_tokens(self, text: str) -> int:
        """Count tokens for the given text using the Gemini API."""
        try:
            ct = self.client.models.count_tokens(
                model=self.model,
                contents=text,
            )
            return ct.total_tokens
        except Exception as e:
            raise e

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
    ) -> AsyncGenerator[str | dict[str, str], None]:
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
                include_thoughts=True,
            ),
            temperature=settings.TEMPERATURE,
        )

        queue: asyncio.Queue[str | dict[str, str] | Exception | None] = asyncio.Queue()
        loop = asyncio.get_event_loop()

        def _extract_parts(chunk: Any) -> list[Any]:
            candidates = getattr(chunk, "candidates", None) or []
            if not candidates:
                content = getattr(chunk, "content", None)
                parts = getattr(content, "parts", None) if content else None
                return list(parts or [])

            extracted_parts: list[Any] = []
            for candidate in candidates:
                content = getattr(candidate, "content", None)
                parts = getattr(content, "parts", None) if content else None
                if parts:
                    extracted_parts.extend(parts)

            return extracted_parts

        def _extract_stream_items(chunk: Any) -> list[str | dict[str, str]]:
            items: list[str | dict[str, str]] = []
            part_text_seen = False

            for part in _extract_parts(chunk):
                text = getattr(part, "text", None)
                if not text:
                    continue

                part_text_seen = True
                if getattr(part, "thought", False):
                    items.append({"type": "think", "chunk": text})
                else:
                    items.append(text)

            chunk_text = getattr(chunk, "text", None)
            if chunk_text and not part_text_seen:
                items.append(chunk_text)

            return items

        def _sync_stream():
            """Run the synchronous Gemini streaming in a thread, pushing chunks to the queue."""
            try:
                for chunk in self.client.models.generate_content_stream(
                    model=self.model,
                    contents=contents,
                    config=generate_content_config,
                ):
                    for item in _extract_stream_items(chunk):
                        loop.call_soon_threadsafe(queue.put_nowait, item)
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
