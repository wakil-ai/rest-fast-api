from __future__ import annotations

from typing import TYPE_CHECKING, Any, Literal

from langgraph.graph import END, START, StateGraph
from langgraph.runtime import Runtime

from src.orchestration import nodes
from src.orchestration.state import GraphContext, RetrievalRewriteState
from src.orchestration.retrieval import resolve_assistant

if TYPE_CHECKING:
    from src.orchestration.service import OrchestrationService


def compile_retrieval_graph(service: OrchestrationService) -> Any:
    builder = StateGraph(RetrievalRewriteState, context_schema=GraphContext)

    def ingest(state: RetrievalRewriteState) -> dict:
        return nodes.ingest_payload(state)

    async def load_files(state: RetrievalRewriteState) -> dict:
        return await nodes.load_file_and_project_context(state, service)

    def load_memory(
        state: RetrievalRewriteState, runtime: Runtime[GraphContext]
    ) -> dict:
        store = getattr(runtime, "store", None)
        if store is None:
            raise RuntimeError(
                "LangGraph runtime has no store (runtime.store is None). "
                "The retrieval graph must be compiled with store=service.store."
            )
        return nodes.load_long_term_memory(state, service, store)

    async def intent(state: RetrievalRewriteState) -> dict:
        return await nodes.recognize_intent(state, service)

    async def court(state: RetrievalRewriteState) -> dict:
        return await nodes.route_court(state, service)

    async def rewrite(state: RetrievalRewriteState) -> dict:
        return await nodes.rewrite_query(state, service)

    async def criminal_subgraph(state: RetrievalRewriteState) -> dict:
        return await nodes.criminal_case_retrieval_subgraph(state)

    async def retrieve(state: RetrievalRewriteState) -> dict:
        return await nodes.retrieve_documents(state, service)

    async def evaluate(state: RetrievalRewriteState) -> dict:
        return await nodes.evaluate_context(state, service)

    async def web_search(state: RetrievalRewriteState) -> dict:
        return await nodes.web_search_fallback(state, service)

    async def answer(state: RetrievalRewriteState) -> dict:
        return await nodes.generate_final_answer(state, service)

    builder.add_node("load_file_and_project_context", load_files)
    builder.add_node("ingest_payload", ingest)
    builder.add_node("load_long_term_memory", load_memory)
    builder.add_node("recognize_intent", intent)
    builder.add_node("route_court", court)
    builder.add_node("rewrite_query", rewrite)
    builder.add_node("criminal_case_retrieval_subgraph", criminal_subgraph)
    builder.add_node("retrieve_documents", retrieve)
    builder.add_node("evaluate_context", evaluate)
    builder.add_node("web_search_fallback", web_search)
    builder.add_node("generate_final_answer", answer)

    builder.add_edge(START, "load_file_and_project_context")
    builder.add_edge("load_file_and_project_context", "ingest_payload")
    builder.add_edge("ingest_payload", "load_long_term_memory")
    builder.add_edge("load_long_term_memory", "rewrite_query")
    builder.add_edge("rewrite_query", "recognize_intent")
    builder.add_edge("recognize_intent", "route_court")

    def route_after_court(
        state: RetrievalRewriteState,
    ) -> Literal["criminal_case_retrieval_subgraph", "retrieve_documents"]:
        if resolve_assistant(state) == "criminal_court":
            return "criminal_case_retrieval_subgraph"
        return "retrieve_documents"

    builder.add_conditional_edges(
        "route_court",
        route_after_court,
        {
            "criminal_case_retrieval_subgraph": "criminal_case_retrieval_subgraph",
            "retrieve_documents": "retrieve_documents",
        },
    )
    builder.add_edge("criminal_case_retrieval_subgraph", "retrieve_documents")

    def route_after_retrieve(
        state: RetrievalRewriteState,
    ) -> Literal["evaluate_context", "generate_final_answer"]:
        if service.web_search_enabled(state):
            return "evaluate_context"
        return "generate_final_answer"

    def route_after_evaluate(
        state: RetrievalRewriteState,
    ) -> Literal["web_search_fallback", "generate_final_answer"]:
        evaluation = state.get("context_evaluation_output") or {}
        if service.web_search.should_run_web_search(
            assistant_name=state.get("assistant_name"),
            deep_research=bool(state.get("deep_research")),
            evaluation=evaluation,
        ):
            return "web_search_fallback"
        return "generate_final_answer"

    builder.add_conditional_edges(
        "retrieve_documents",
        route_after_retrieve,
        {
            "evaluate_context": "evaluate_context",
            "generate_final_answer": "generate_final_answer",
        },
    )
    builder.add_conditional_edges(
        "evaluate_context",
        route_after_evaluate,
        {
            "web_search_fallback": "web_search_fallback",
            "generate_final_answer": "generate_final_answer",
        },
    )
    builder.add_edge("web_search_fallback", "generate_final_answer")
    builder.add_edge("generate_final_answer", END)

    return builder.compile(
        checkpointer=service.checkpointer,
        store=service.store,
    )
