# app/api/ws_stt.py
import json
import struct
from typing import List, Optional

from fastapi import APIRouter, WebSocket, WebSocketDisconnect, HTTPException, status
from app.services.streaming_speech_to_text import get_streaming_stt_service
from app.core.config import settings

router = APIRouter(prefix="/ws", tags=["Speech-to-Text (WS)"])


def _verify_api_key_in_query(query_params) -> None:
    api_key = query_params.get("api_key")
    if not api_key or api_key != settings.API_KEY:
        # WebSocket can't easily return 401 with headers, so close with code + reason
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or missing API Key in query (?api_key=...)",
        )


@router.websocket("/stt")
async def stt_websocket(ws: WebSocket):
    # Authenticate via query param
    try:
        _verify_api_key_in_query(ws.query_params)
    except HTTPException as e:
        # Accept then close with error message (so client sees reason)
        await ws.accept()
        await ws.close(code=4000, reason=e.detail)
        return

    await ws.accept()

    service = None
    started = False

    try:
        # Protocol:
        # 1) First message must be JSON:
        #    { "event": "start", "provider":"google|azure", "language":"uz-UZ", "hints":["..."] }
        # 2) Subsequent messages:
        #    - Binary audio frames (PCM16 mono 16kHz)
        #    - JSON { "event": "stop" } to end
        #
        # Server sends JSON messages:
        #    { "type":"partial"|"final", "text":"..." }
        #    { "type":"error", "message":"..." }

        # Wait for start
        first = await ws.receive()
        if "text" not in first:
            await ws.send_text(json.dumps({"type": "error", "message": "First frame must be JSON 'start'"}))
            await ws.close(code=1002)
            return

        start_msg = json.loads(first["text"])
        if not isinstance(start_msg, dict) or start_msg.get("event") != "start":
            await ws.send_text(json.dumps({"type": "error", "message": "Expected event='start' JSON"}))
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

        import asyncio
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
                    await ws.send_text(json.dumps({"type": "error", "message": "Invalid JSON"}))
                    continue

                if payload.get("event") == "stop":
                    await service.finalize()
                    await pump_task
                    await ws.close(code=1000)
                    break
                else:
                    await ws.send_text(json.dumps({"type": "error", "message": "Unknown event"}))
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
