import asyncio
import inspect
import queue as _q
import threading
from abc import ABC, abstractmethod
from collections.abc import AsyncGenerator
from functools import lru_cache

from app.core.config import settings


class StreamingSTTService(ABC):
    """
    Base interface for a streaming STT provider.
    """

    @abstractmethod
    async def start(self, language: str, hints: list[str] | None = None) -> None:
        """
        Prepare/initialize streaming with a language code and optional hints.
        """
        ...

    @abstractmethod
    async def feed_audio(self, chunk: bytes) -> None:
        """
        Feed raw PCM audio bytes (16kHz, 16-bit, mono).
        """
        ...

    @abstractmethod
    async def finalize(self) -> None:
        """
        Signal no more audio will be sent. Clean up resources.
        """
        ...

    @abstractmethod
    async def results(self) -> AsyncGenerator[dict, None]:
        """
        Async stream of recognition results as dicts:
        { "type": "partial"|"final", "text": "..." }
        """
        ...


class GoogleStreamingSTTService(StreamingSTTService):
    """
    Google Cloud Speech-to-Text Streaming using streaming_recognize (gRPC).
    """

    def __init__(self) -> None:
        from google.cloud import speech

        self._speech = speech
        self._client = speech.SpeechClient()

        # Queues
        self._audio_q: asyncio.Queue[bytes | None] = asyncio.Queue()
        self._result_q: asyncio.Queue[dict] = asyncio.Queue()

        # Task
        self._producer_task: asyncio.Task | None = None

        # Config placeholders
        self._language = "en-US"
        self._hints: list[str] = []

    async def start(self, language: str, hints: list[str] | None = None) -> None:
        self._language = language
        self._hints = hints or []
        loop = asyncio.get_running_loop()
        self._producer_task = loop.create_task(self._run_stream())

    async def feed_audio(self, chunk: bytes) -> None:
        await self._audio_q.put(chunk)

    async def finalize(self) -> None:
        await self._audio_q.put(None)
        if self._producer_task:
            await self._producer_task

    async def results(self) -> AsyncGenerator[dict, None]:
        while True:
            item = await self._result_q.get()
            if item is None:
                break
            yield item

    async def _run_stream(self) -> None:
        # Build classic SpeechContext hints (works across versions)
        speech_contexts = []
        if self._hints:
            speech_contexts.append(self._speech.SpeechContext(phrases=self._hints))

        config = self._speech.RecognitionConfig(
            encoding=self._speech.RecognitionConfig.AudioEncoding.LINEAR16,
            sample_rate_hertz=16000,
            language_code=self._language,
            enable_automatic_punctuation=True,
            speech_contexts=speech_contexts,
        )

        streaming_config = self._speech.StreamingRecognitionConfig(
            config=config,
            interim_results=True,
            single_utterance=False,
        )

        # --- Generators ---
        async def _gen_with_config_first():
            # 1) send config in first request
            yield self._speech.StreamingRecognizeRequest(
                streaming_config=streaming_config
            )
            # 2) then audio chunks
            while True:
                chunk = await self._audio_q.get()
                if chunk is None:
                    break
                yield self._speech.StreamingRecognizeRequest(audio_content=chunk)

        async def _gen_audio_only():
            # Only audio requests; config will be passed as a separate arg (v2 signatures)
            while True:
                chunk = await self._audio_q.get()
                if chunk is None:
                    break
                yield self._speech.StreamingRecognizeRequest(audio_content=chunk)

        def _iter_from_async(async_gen):
            q_out: _q.Queue[object] = _q.Queue(maxsize=32)
            _STOP = object()

            async def _pump():
                try:
                    async for item in async_gen:
                        q_out.put(item)
                finally:
                    q_out.put(_STOP)

            threading.Thread(target=lambda: asyncio.run(_pump()), daemon=True).start()

            while True:
                item = q_out.get()
                if item is _STOP:
                    break
                yield item

        try:
            # Detect which signature we have:
            # - v1 style:   streaming_recognize(requests=iterator)
            # - v2 style:   streaming_recognize(config=..., requests=iterator)  (or positional)
            sig = inspect.signature(self._client.streaming_recognize)
            params = list(sig.parameters.values())
            param_names = [p.name for p in params]

            use_two_args = False
            if len(params) >= 2:
                # If first parameters look like (config, requests) or named 'config' is present
                use_two_args = (param_names[0] in ("config", "streaming_config")) or (
                    "config" in param_names
                )

            if use_two_args:
                # v2 style: pass config separately, audio-only iterator
                responses = self._client.streaming_recognize(
                    streaming_config,  # or config=streaming_config
                    _iter_from_async(_gen_audio_only()),
                )
            else:
                # v1 style: put config in first request
                responses = self._client.streaming_recognize(
                    requests=_iter_from_async(_gen_with_config_first())
                )

            for response in responses:
                for result in response.results:
                    if not result.alternatives:
                        continue
                    text = result.alternatives[0].transcript
                    if result.is_final:
                        await self._result_q.put({"type": "final", "text": text})
                    else:
                        await self._result_q.put({"type": "partial", "text": text})

        finally:
            await self._result_q.put(None)


class AzureStreamingSTTService(StreamingSTTService):
    """
    Azure Cognitive Services Speech (Continuous Recognition + PushAudioInputStream).
    """

    def __init__(self) -> None:
        import azure.cognitiveservices.speech as speechsdk

        self._sdk = speechsdk
        self._speech_config = speechsdk.SpeechConfig(
            subscription=settings.AZURE_SPEECH_KEY,
            region=settings.AZURE_SPEECH_REGION,
        )

        self._result_q: asyncio.Queue[dict] = asyncio.Queue()
        self._push_stream = None
        self._audio_config = None
        self._recognizer = None
        self._started = False
        self._loop: asyncio.AbstractEventLoop | None = None
        self._closed_once = False

    async def start(self, language: str, hints: list[str] | None = None) -> None:
        self._speech_config.speech_recognition_language = language
        self._loop = asyncio.get_running_loop()

        self._push_stream = self._sdk.audio.PushAudioInputStream()
        self._audio_config = self._sdk.audio.AudioConfig(stream=self._push_stream)
        self._recognizer = self._sdk.SpeechRecognizer(
            speech_config=self._speech_config,
            audio_config=self._audio_config,
        )

        # Hints (PhraseListGrammar uses camelCase addPhrase)
        if hints:
            try:
                plg = self._sdk.PhraseListGrammar.from_recognizer(self._recognizer)
                for h in hints:
                    try:
                        plg.addPhrase(h)
                    except Exception:
                        pass
            except Exception:
                pass

        def _post(item: dict | None):
            if self._loop is not None:
                asyncio.run_coroutine_threadsafe(self._result_q.put(item), self._loop)

        def recognizing(evt):
            if evt.result and evt.result.text:
                _post({"type": "partial", "text": evt.result.text})

        def recognized(evt):
            if evt.result and evt.result.text:
                _post({"type": "final", "text": evt.result.text})

        def canceled(evt):
            msg = f"Azure STT canceled: {evt.reason} - {getattr(evt, 'error_details', '')}"
            _post({"type": "error", "message": msg})
            if not self._closed_once:
                self._closed_once = True
                _post(None)

        self._recognizer.recognizing.connect(recognizing)
        self._recognizer.recognized.connect(recognized)
        self._recognizer.canceled.connect(canceled)

        # IMPORTANT: start_continuous_recognition_async returns a ResultFuture -> call .get() off-loop
        loop = asyncio.get_running_loop()

        def _start_blocking():
            self._recognizer.start_continuous_recognition_async().get()

        await loop.run_in_executor(None, _start_blocking)

        self._started = True

    async def feed_audio(self, chunk: bytes) -> None:
        if self._push_stream:
            self._push_stream.write(chunk)

    async def finalize(self) -> None:
        if not self._started:
            if not self._closed_once:
                self._closed_once = True
                await self._result_q.put(None)
            return

        # Close push stream and stop recognition (both are blocking .get() calls)
        def _stop_blocking():
            try:
                if self._push_stream:
                    self._push_stream.close()
                if self._recognizer:
                    self._recognizer.stop_continuous_recognition_async().get()
            except Exception:
                pass

        loop = asyncio.get_running_loop()
        await loop.run_in_executor(None, _stop_blocking)

        if not self._closed_once:
            self._closed_once = True
            await self._result_q.put(None)

    async def results(self) -> AsyncGenerator[dict, None]:
        while True:
            item = await self._result_q.get()
            if item is None:
                break
            yield item


@lru_cache
def get_streaming_stt_service(provider: str) -> StreamingSTTService:
    provider = (provider or "").lower()
    if provider == "google":
        return GoogleStreamingSTTService()
    if provider == "azure":
        return AzureStreamingSTTService()
    raise ValueError(f"Unknown streaming provider: {provider}")
