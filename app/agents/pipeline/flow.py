from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any

from app.agents.pipeline.last_answer import run_last_answer
from app.agents.pipeline.retrieval_runner import ContextRetrievalRunner
from app.agents.pipeline.schemas import ChatPipelineState
from app.core.dependencies import get_chat_orchestrator


class AgenticRAGFlow:
    """
    Two-stage RAG: LangGraph context retrieval, then a single final-answer generation.
    """

    def __init__(
        self,
        enable_progress_stream: bool = False,
        progress_callback: Callable[[dict[str, Any]], Awaitable[None]] | None = None,
    ) -> None:
        self.enable_progress_stream = enable_progress_stream
        self.progress_callback = progress_callback
        self.state = ChatPipelineState()

    async def kickoff_async(
        self, initial_state: ChatPipelineState | dict[str, Any]
    ) -> str:
        if isinstance(initial_state, dict):
            self.state = ChatPipelineState.model_validate(initial_state)
        else:
            self.state = initial_state

        runner = ContextRetrievalRunner()
        await runner.run(self.state, get_chat_orchestrator().get_agent)

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
            progress_callback=self.progress_callback,
            stream=self.enable_progress_stream,
        )
