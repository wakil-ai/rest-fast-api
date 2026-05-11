from typing import Any

from crewai.flow.flow import Flow, listen, start

from app.agents.pipeline.last_answer import run_last_answer
from app.agents.pipeline.retrieval_runner import ContextRetrievalRunner
from app.core.config import settings
from app.core.dependencies import get_chat_orchestrator
from app.orchestration.schemas import ChatPipelineState
from app.utils.streaming import format_progress_event


class AgenticRAGFlow(Flow[ChatPipelineState]):
    """
    Two-stage pipeline: context retrieval (history, strategy, corpus, eval, web)
    then a single final-answer generation call.
    """

    def __init__(self, enable_progress_stream: bool = False, progress_callback=None):
        super().__init__(tracing=settings.TRACING)
        self.enable_progress_stream = enable_progress_stream
        self.progress_callback = progress_callback

    async def _emit_progress(
        self,
        event_type: str,
        status: str,
        message: str,
        details: dict[str, Any] | None = None,
    ) -> None:
        if self.progress_callback:
            event = await format_progress_event(event_type, status, message, details)
            await self.progress_callback(event)

    @start()
    async def run_context_retrieval(self) -> None:
        runner = ContextRetrievalRunner(progress_callback=self._emit_progress)
        await runner.run(self.state, get_chat_orchestrator().get_agent)

    @listen(run_context_retrieval)
    async def run_final_answer(self) -> str:
        from app.agents.pipeline.chat_turn import (
            get_attach_agent_for_pipeline,
            get_generation_agent_for_pipeline,
        )

        get_agent = get_chat_orchestrator().get_agent
        gen = get_generation_agent_for_pipeline(self.state)
        attach = get_attach_agent_for_pipeline(self.state, get_agent)
        return await run_last_answer(
            self.state,
            generation_agent=gen,
            attach_agent=attach,
            progress_callback=self._emit_progress,
            stream=self.enable_progress_stream,
        )
