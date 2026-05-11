"""
LangGraph ``StateGraph`` for the context-retrieval phase (chat/ask + agentic RAG).

Topology: ``strategy`` → ``fetch`` → ``evaluate`` →
(``web_search`` if insufficient) → ``END``.

Conversation context for the final LLM is loaded separately via
``agent_session_thread_id`` (see ``build_pipeline_thread_chat_history``), not here.
"""

from __future__ import annotations

from typing import Any, Literal, TYPE_CHECKING

from langgraph.graph import END, START, StateGraph

from app.agents.pipeline.schemas import ChatPipelineState

if TYPE_CHECKING:
    from app.agents.pipeline.retrieval_runner import ContextRetrievalRunner


def compile_context_retrieval_graph(runner: ContextRetrievalRunner) -> Any:
    """Build and compile the retrieval subgraph; nodes delegate to ``runner`` methods."""
    builder = StateGraph(dict)

    async def node_strategy(s: dict[str, Any]) -> dict[str, Any]:
        m = ChatPipelineState.model_validate(s)
        await runner._strategy_or_route_phase(m)
        return m.model_dump(mode="python")

    async def node_fetch(s: dict[str, Any]) -> dict[str, Any]:
        m = ChatPipelineState.model_validate(s)
        await runner._fetch_documents_phase(m)
        return m.model_dump(mode="python")

    async def node_evaluate(s: dict[str, Any]) -> dict[str, Any]:
        m = ChatPipelineState.model_validate(s)
        await runner._evaluate_context_sufficiency(m)
        return m.model_dump(mode="python")

    async def node_web(s: dict[str, Any]) -> dict[str, Any]:
        m = ChatPipelineState.model_validate(s)
        await runner._perform_web_search(m)
        return m.model_dump(mode="python")

    builder.add_node("strategy", node_strategy)
    builder.add_node("fetch", node_fetch)
    builder.add_node("evaluate", node_evaluate)
    builder.add_node("web_search", node_web)

    builder.add_edge(START, "strategy")
    builder.add_edge("strategy", "fetch")
    builder.add_edge("fetch", "evaluate")

    def route_after_evaluate(
        s: dict[str, Any],
    ) -> Literal["needs_web", "done"]:
        ev = s.get("context_evaluation_output") or {}
        if ev.get("is_sufficient") is False:
            return "needs_web"
        return "done"

    builder.add_conditional_edges(
        "evaluate",
        route_after_evaluate,
        {"needs_web": "web_search", "done": END},
    )
    builder.add_edge("web_search", END)
    return builder.compile()
