from __future__ import annotations

from collections.abc import AsyncGenerator
from typing import Any

from app.agents import (
    AdministrativeCourtAgent,
    BaseAgent,
    CivilCourtAgent,
    ContractAnalyzerAgent,
    CourtAgent,
    CriminalCourtAgent,
    EconomicCourtAgent,
    MainAgent,
    TaxAgent,
)
from app.agents.common.state import AgentRequestContext, AgentRunResult
from app.core.assistants import AssistantConfig


class ChatOrchestrator:
    """Thin router that delegates complete chat turns to assistant agents."""

    AGENT_REGISTRY: dict[str, type[BaseAgent]] = {
        "main": MainAgent,
        "umumiy": MainAgent,
        "tax": TaxAgent,
        "court": CourtAgent,
        "administrative_court": AdministrativeCourtAgent,
        "contract_analyzer": ContractAnalyzerAgent,
        "criminal_court": CriminalCourtAgent,
        "economic_court": EconomicCourtAgent,
        "civil_court": CivilCourtAgent,
    }

    def __init__(self):
        self._agent_cache: dict[str, BaseAgent] = {}

    def get_agent(self, assistant_name: str | None) -> BaseAgent:
        canonical = AssistantConfig.validate_assistant_or_default(assistant_name)
        if canonical not in self._agent_cache:
            cls = self.AGENT_REGISTRY.get(canonical, MainAgent)
            self._agent_cache[canonical] = cls()
        return self._agent_cache[canonical]

    # Internal runtime tools still use this hook for tool-call retrieval.
    def _get_agent(self, assistant_name: str | None) -> BaseAgent:
        return self.get_agent(assistant_name)

    async def invoke(
        self,
        request: AgentRequestContext,
    ) -> AgentRunResult:
        return await self.get_agent(request.assistant).ainvoke(request)

    async def astream(
        self,
        request: AgentRequestContext,
    ) -> AsyncGenerator[str | dict[str, Any], None]:
        async for item in self.get_agent(request.assistant).astream(request):
            yield item

    async def generate_answer(
        self,
        user_id: str,
        session_id: str,
        query: str,
        stream: bool,
        file_ids: list[str] | None = None,
        assistant: str = "main",
        project_id: str | None = None,
        message_id: str = "",
    ):
        """Backward-compatible wrapper for older service paths."""
        request = AgentRequestContext(
            user_id=user_id,
            session_id=session_id,
            message_id=message_id or "legacy",
            query=query,
            assistant=assistant,
            file_ids=file_ids,
            project_id=project_id,
            stream=stream,
        )
        if stream:
            return self.astream(request)
        result = await self.invoke(request)
        return result.answer, result.metadata
