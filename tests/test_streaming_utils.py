import asyncio
import json

import pytest

from app.utils.streaming import format_streaming_response


@pytest.mark.asyncio
async def test_format_streaming_response_emits_keepalive(monkeypatch):
    import app.utils.streaming as streaming_module

    monkeypatch.setattr(
        streaming_module.settings,
        "STREAM_KEEPALIVE_INTERVAL_SECONDS",
        0.01,
    )

    async def delayed_generator():
        await asyncio.sleep(0.03)
        yield "done"

    events = []
    async for frame in format_streaming_response(delayed_generator()):
        if frame.startswith("data: "):
            events.append(json.loads(frame.removeprefix("data: ").strip()))

    assert {"type": "progress", "message": "still working"} in events
    assert {"type": "chunk", "chunk": "done"} in events
    assert events[-1] == {"type": "end"}
