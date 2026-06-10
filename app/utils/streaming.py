import asyncio
import json
from collections.abc import AsyncGenerator
from typing import Any, Optional

from app.core.config import settings
from app.core.logger import logger

SUPPORTED_STREAM_EVENT_TYPES = {
    "progress",
    "chunk",
    "think",
    "attachments",
    "metadata",
    "error",
    "end",
}
KEEPALIVE_EVENT = {"type": "progress", "message": "still working"}
STREAM_END = object()


def _format_sse_message(item: Any) -> str:
    if isinstance(item, dict):
        if item.get("type") in SUPPORTED_STREAM_EVENT_TYPES:
            return f"data: {json.dumps(item)}\n\n"

        debug_event = {"type": "debug", "data": item}
        return f"data: {json.dumps(debug_event)}\n\n"

    chunk_data = {"type": "chunk", "chunk": item}
    return f"data: {json.dumps(chunk_data)}\n\n"


async def _pump_stream_items(
    response_generator: AsyncGenerator[Any, None], queue: asyncio.Queue[Any]
) -> None:
    try:
        async for item in response_generator:
            await queue.put(item)
    except Exception as exc:
        await queue.put(exc)
    finally:
        await queue.put(STREAM_END)


async def format_streaming_response(
    response_generator: AsyncGenerator[Any, None],
) -> AsyncGenerator[str, None]:
    """
    Format streaming response as Server-Sent Events.
    Supports both text chunks and debug data events.

    Args:
        response_generator: AsyncGenerator that yields text chunks or dict with debug data

    Yields:
        str: Formatted SSE data strings

    Event types:
        - debug: Development mode debug data (sent once at start if available)
        - progress: Agentic RAG step progress updates
        - progress: Keepalive heartbeat while long work is still running
        - chunk: Character chunks of the answer
        - end: Stream completion signal
        - error: Error message
    """
    queue: asyncio.Queue[Any] = asyncio.Queue()
    producer_task = asyncio.create_task(_pump_stream_items(response_generator, queue))

    try:
        while True:
            try:
                item = await asyncio.wait_for(
                    queue.get(),
                    timeout=settings.STREAM_KEEPALIVE_INTERVAL_SECONDS,
                )
            except asyncio.TimeoutError:
                yield _format_sse_message(KEEPALIVE_EVENT)
                continue

            if item is STREAM_END:
                break

            if isinstance(item, Exception):
                raise item

            if isinstance(item, dict) and item.get("type") == "end":
                yield _format_sse_message(item)
                break

            yield _format_sse_message(item)

        else:
            # Generator finished without an explicit end event
            yield _format_sse_message({"type": "end"})

    except Exception as e:
        # Log the full traceback before swallowing it into the SSE stream.
        # Without this, the client only ever sees ``str(e)`` (e.g. "'error'" for a
        # bare ``KeyError('error')``) and the originating frame is lost.
        logger.opt(exception=e).error(
            f"[streaming] response generator failed: {type(e).__name__}: {e}"
        )
        # Send error in streaming format
        error_data = {"type": "error", "error": str(e)}
        yield _format_sse_message(error_data)
    finally:
        if not producer_task.done():
            producer_task.cancel()

        try:
            await producer_task
        except asyncio.CancelledError:
            pass


async def format_progress_event(
    event_type: str, status: str, message: str, details: Optional[dict] = None
) -> dict:
    """
    Format a progress event for streaming.

    Args:
        event_type: Type of event (memory_retrieval, retrieval_strategy, etc.)
        status: Status of the event (in_progress, completed, failed)
        message: User-friendly message describing the step

    Returns:
        dict: Formatted progress event ready for streaming
    """
    event = {
        "type": "progress",
        "event_type": event_type,
        "status": status,
        "message": message,
    }

    return event


def get_streaming_headers() -> dict:
    """
    Get headers required for streaming response.

    Returns:
        dict: Dictionary of headers for SSE streaming
    """
    return {
        "Cache-Control": "no-cache, no-store, must-revalidate",
        "Pragma": "no-cache",
        "Expires": "0",
        "Connection": "keep-alive",
        "Access-Control-Allow-Origin": "*",
        "Access-Control-Allow-Methods": "GET, POST, OPTIONS",
        "Access-Control-Allow-Headers": "*",
        "X-Accel-Buffering": "no",  # Disable nginx buffering
        "Transfer-Encoding": "chunked",  # Ensure chunked encoding
        "Content-Encoding": "identity",  # Disable compression
    }
