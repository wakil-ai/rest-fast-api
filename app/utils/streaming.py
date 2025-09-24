from typing import AsyncGenerator
import json
import asyncio


async def format_streaming_response(response_generator: AsyncGenerator[str, None]) -> AsyncGenerator[str, None]:
    """
    Format streaming response as Server-Sent Events.
    
    Args:
        response_generator: AsyncGenerator that yields text chunks
        
    Yields:
        str: Formatted SSE data strings
    """
    try:
        async for chunk in response_generator:
            # Send text chunk immediately without delay
            chunk_data = {"type": "chunk", "chunk": chunk}
            yield f"data: {json.dumps(chunk_data)}\n\n"
        
        # Send completion signal
        end_signal = {"type": "end"}
        yield f"data: {json.dumps(end_signal)}\n\n"
        
    except Exception as e:
        # Send error in streaming format
        error_data = {"type": "error", "error": str(e)}
        yield f"data: {json.dumps(error_data)}\n\n"


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