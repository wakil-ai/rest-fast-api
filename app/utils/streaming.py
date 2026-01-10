import asyncio
import json
from collections.abc import AsyncGenerator


async def format_streaming_response(
    response_generator: AsyncGenerator[str, None],
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
        - chunk: Character chunks of the answer
        - end: Stream completion signal
        - error: Error message
    """
    try:
        async for item in response_generator:
            # Handle debug data (sent as dict)
            if isinstance(item, dict):
                # Check if this is a progress or chunk event
                if item.get("type") in ["progress", "chunk"]:
                    yield f"data: {json.dumps(item)}\n\n"
                else:
                    debug_event = {"type": "debug", "data": item}
                    yield f"data: {json.dumps(debug_event)}\n\n"
            # Handle text chunks
            else:
                for char in item:
                    char_data = {"type": "chunk", "chunk": char}
                    yield f"data: {json.dumps(char_data)}\n\n"

        # Send completion signal
        end_signal = {"type": "end"}
        yield f"data: {json.dumps(end_signal)}\n\n"

    except Exception as e:
        # Send error in streaming format
        error_data = {"type": "error", "error": str(e)}
        yield f"data: {json.dumps(error_data)}\n\n"


async def format_progress_event(
    event_type: str, status: str, message: str, details: dict = None
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
