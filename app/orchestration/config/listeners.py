from crewai.events import BaseEventListener, LLMStreamChunkEvent


class AgenticRAGStreamListener(BaseEventListener):
    def setup_listeners(self, crewai_event_bus):
        @crewai_event_bus.on(LLMStreamChunkEvent)
        def on_llm_stream_chunk(self, event: LLMStreamChunkEvent):
            # Process each chunk as it arrives
            yield event.chunk.text
