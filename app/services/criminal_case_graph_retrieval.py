"""Criminal-case graph context for the criminal-court assistant (Neo4j + Mongo)."""

from __future__ import annotations

import asyncio
import concurrent.futures
import logging

from langchain_core.messages import HumanMessage, SystemMessage

from app.core.dependencies import get_orchestration_service
from app.orchestration.prompts import abuild_criminal_system_prompt
from app.orchestration.agents.criminal_retrieval_langgraph import arun_criminal_retrieval
from app.orchestration.text import message_content_to_plain_str

logger = logging.getLogger(__name__)


class CriminalCaseGraphRetriever:
    """Builds grounded context from the criminal case vector index (and Mongo excerpts)."""

    async def abuild_context(self, query: str) -> str:
        return await arun_criminal_retrieval(query)

    def build_context(self, query: str) -> str:
        """Sync entrypoint for ``asyncio.to_thread`` (see ``CriminalCourtAgent``)."""

        def _runner() -> str:
            return asyncio.run(self.abuild_context(query))

        try:
            asyncio.get_running_loop()
        except RuntimeError:
            return _runner()
        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
            return pool.submit(_runner).result(timeout=600.0)

    async def aanswer_with_retrieval(self, query: str) -> str:
        """Run MODE routing, case retrieval, composed v2 system prompt, and generation."""
        system, _modes = await abuild_criminal_system_prompt(
            query,
            context=(
                "Three similar cases are listed under RETRIEVED SIMILAR CRIMINAL CASES above. "
                "Use `search_criminal_case_graph` / `search_legal_corpus` if you need more."
            ),
            chat_history="Use `get_chat_history` when multi-turn context is required.",
        )
        from app.core.langfuse_tracing import LlmRunName, traced_ainvoke

        llm = get_orchestration_service().generation_llm
        response = await traced_ainvoke(
            llm,
            [SystemMessage(content=system), HumanMessage(content=query)],
            run_name=LlmRunName.CRIMINAL_COURT_ANSWER,
        )
        return message_content_to_plain_str(getattr(response, "content", response))

    def answer_with_retrieval(self, query: str) -> str:
        """Sync wrapper for scripts / tooling."""

        def _runner() -> str:
            return asyncio.run(self.aanswer_with_retrieval(query))

        try:
            asyncio.get_running_loop()
        except RuntimeError:
            return _runner()
        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
            return pool.submit(_runner).result(timeout=600.0)
