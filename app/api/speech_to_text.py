import asyncio
import json

from fastapi import (
    APIRouter,
    File,
    Form,
    HTTPException,
    UploadFile,
    WebSocket,
    WebSocketDisconnect,
)

from app.core.logger import logger
from app.models.speech_to_text import TranscriptionResponse
from app.services.speech_to_text_service import get_speech_to_text_service
from app.services.streaming_speech_to_text import (
    get_streaming_stt_service,
)  # for WS STT

router = APIRouter(prefix="/speech-to-text", tags=["Speech-to-Text"])


@router.post("/transcribe", response_model=TranscriptionResponse)
def transcribe_audio(
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
        speech_to_text_service = get_speech_to_text_service()

        text = speech_to_text_service.transcribe_audio(file.file, language)
        return TranscriptionResponse(text=text)
    except Exception as e:
        logger.error(f"[SpeechToTextAPI] Error: {str(e)}")
        raise HTTPException(
            status_code=500,
            detail="Failed to transcribe audio. Please try again later.",
        )


@router.websocket("/ws/transcribe")
async def stt_websocket(ws: WebSocket):
    await ws.accept()

    service = None
    started = False

    try:
        first = await ws.receive()
        if "text" not in first:
            await ws.send_text(
                json.dumps(
                    {"type": "error", "message": "First frame must be JSON 'start'"}
                )
            )
            await ws.close(code=1002)
            return

        start_msg = json.loads(first["text"])
        if not isinstance(start_msg, dict) or start_msg.get("event") != "start":
            await ws.send_text(
                json.dumps({"type": "error", "message": "Expected event='start' JSON"})
            )
            await ws.close(code=1002)
            return

        provider = (start_msg.get("provider") or "azure").lower()
        language = start_msg.get("language") or "en-US"
        hints = start_msg.get("hints") or []

        service = get_streaming_stt_service(provider)
        await service.start(language=language, hints=hints)
        started = True

        # Kick off a task to pump results back to client
        async def result_pump():
            try:
                async for item in service.results():
                    await ws.send_text(json.dumps(item, ensure_ascii=False))
            except Exception as e:
                await ws.send_text(json.dumps({"type": "error", "message": str(e)}))

        pump_task = asyncio.create_task(result_pump())

        # Receive loop
        while True:
            msg = await ws.receive()

            if "bytes" in msg and msg["bytes"] is not None:
                # Binary audio frame: raw PCM16
                await service.feed_audio(msg["bytes"])
                continue

            if "text" in msg and msg["text"] is not None:
                try:
                    payload = json.loads(msg["text"])
                except Exception:
                    await ws.send_text(
                        json.dumps({"type": "error", "message": "Invalid JSON"})
                    )
                    continue

                if payload.get("event") == "stop":
                    await service.finalize()
                    await pump_task
                    await ws.close(code=1000)
                    break
                else:
                    await ws.send_text(
                        json.dumps({"type": "error", "message": "Unknown event"})
                    )
                    continue

    except WebSocketDisconnect:
        # Client disconnected; ensure cleanup
        if started and service:
            await service.finalize()
    except Exception as e:
        # Bubble error to client
        try:
            await ws.send_text(json.dumps({"type": "error", "message": str(e)}))
        finally:
            await ws.close(code=1011)
