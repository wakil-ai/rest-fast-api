import asyncio
import json
from urllib.parse import urlparse, urlunparse

from fastapi import (
    APIRouter,
    File,
    Form,
    HTTPException,
    UploadFile,
    WebSocket,
    WebSocketDisconnect,
)
import websockets

from app.core.config import settings
from app.core.dependencies import get_llm_service_client
from app.core.logger import logger
from app.models.speech_to_text import TranscriptionResponse

router = APIRouter(prefix="/speech-to-text", tags=["Speech-to-Text"])


def _llm_ws_url(path: str) -> str:
    base = (settings.LLM_SERVICE_URL or "").strip().rstrip("/")
    parsed = urlparse(base)
    scheme = "wss" if parsed.scheme == "https" else "ws"
    return urlunparse(parsed._replace(scheme=scheme, path=path, params="", query="", fragment=""))


@router.post("/transcribe", response_model=TranscriptionResponse)
async def transcribe_audio(
    file: UploadFile = File(...),
    language: str = Form(...),
):
    """
    Transcribes audio to text using the configured speech-to-text provider.

    - **file**: The audio file to transcribe.
    - **language**: The language of the audio (e.g., 'uz-UZ', 'ru-RU', 'en-US').
    - **hints**: A comma-separated string of words or phrases to improve recognition accuracy.
    """
    try:
        result = await get_llm_service_client().transcribe_audio(
            file=file.file,
            filename=file.filename or "audio",
            content_type=file.content_type or "application/octet-stream",
            language=language,
        )
        return TranscriptionResponse(text=str(result.get("text") or ""))
    except Exception as e:
        logger.error(f"[SpeechToTextAPI] Error: {str(e)}")
        raise HTTPException(
            status_code=500,
            detail="Failed to transcribe audio. Please try again later.",
        )


@router.websocket("/ws/transcribe")
async def stt_websocket(ws: WebSocket):
    await ws.accept()

    try:
        headers = {}
        if settings.LLM_SERVICE_INTERNAL_TOKEN:
            headers[settings.LLM_SERVICE_INTERNAL_HEADER] = settings.LLM_SERVICE_INTERNAL_TOKEN

        async with websockets.connect(
            _llm_ws_url("/api/v1/speech-to-text/ws/transcribe"),
            additional_headers=headers,
            open_timeout=10,
            close_timeout=10,
        ) as upstream:
            async def client_to_upstream():
                while True:
                    msg = await ws.receive()
                    if "bytes" in msg and msg["bytes"] is not None:
                        await upstream.send(msg["bytes"])
                    elif "text" in msg and msg["text"] is not None:
                        await upstream.send(msg["text"])

            async def upstream_to_client():
                async for message in upstream:
                    if isinstance(message, bytes):
                        await ws.send_bytes(message)
                    else:
                        await ws.send_text(message)

            await asyncio.gather(client_to_upstream(), upstream_to_client())

    except WebSocketDisconnect:
        return
    except Exception as e:
        try:
            await ws.send_text(json.dumps({"type": "error", "message": str(e)}))
        finally:
            await ws.close(code=1011)
