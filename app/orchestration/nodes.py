from __future__ import annotations

from collections.abc import AsyncGenerator
from typing import TYPE_CHECKING, Any

from langchain_core.messages import (
    AIMessage,
    AIMessageChunk,
    BaseMessage,
    HumanMessage,
)

from app.core.assistants import AssistantConfig
from app.core.langfuse_tracing import (
    LlmRunName,
    langchain_invoke_config,
    traced_ainvoke,
)
from app.orchestration.retrieval import (
    resolve_assistant,
    retrieve_for_assistant,
)
from app.orchestration.state import RetrievalRewriteState
from app.orchestration.text import (
    history_text_from_state,
    message_content_to_plain_str,
)
from app.orchestration.utils import load_turn_file_context, save_orchestration_llm_context_json

if TYPE_CHECKING:
    from app.orchestration.service import OrchestrationService


def ingest_payload(state: RetrievalRewriteState) -> dict:
    return {"messages": [HumanMessage(content=state["query"])]}


async def load_file_and_project_context(
    state: RetrievalRewriteState, runtime: OrchestrationService
) -> dict:
    _ = runtime
    return await load_turn_file_context(state)


def load_long_term_memory(
    state: RetrievalRewriteState, service: OrchestrationService, store
) -> dict:
    user_id = service.context.user_id
    namespace = ("legal_assistant", "users", user_id, "memories")
    try:
        memories = store.search(namespace, limit=5)
    except Exception as exc:
        raise RuntimeError(
            f"Long-term memory lookup failed for user_id={user_id!r}: {exc}"
        ) from exc
    memory_text = "\n".join(str(item.value) for item in memories)
    return {"long_term_memory": memory_text}


async def recognize_intent(
    state: RetrievalRewriteState, runtime: OrchestrationService
) -> dict:
    assistant = resolve_assistant(state)
    if assistant not in {"contract_analyzer", "contract"}:
        return {}

    history = history_text_from_state(state)
    domain, _prompt, intent = await runtime.intent_classifier.classify_intent(
        state["query"],
        chat_history=history,
        file_context=state.get("file_context") or "",
        llm=runtime.lite_llm,
        user_id=str(state.get("user_id") or "") or None,
        session_id=str(state.get("session_id") or "") or None,
    )
    return {
        "intent_domain": domain,
        "legal_intent": intent.value,
        "selected_assistant": "contract_analyzer",
    }


async def route_court(
    state: RetrievalRewriteState, runtime: OrchestrationService
) -> dict:
    assistant = resolve_assistant(state)
    if assistant not in {"court", "administrative_court"}:
        return {}

    history = history_text_from_state(state)
    decision = await runtime.court_classifier.route_query(
        state["query"],
        chat_history=history,
        file_context=state.get("file_context") or "",
        llm=runtime.lite_llm,
        user_id=str(state.get("user_id") or "") or None,
        session_id=str(state.get("session_id") or "") or None,
    )
    return {
        "selected_assistant": decision.assistant_name or "administrative_court",
        "court_route_tag": decision.court_route_tag,
    }


async def rewrite_query(
    state: RetrievalRewriteState, runtime: OrchestrationService
) -> dict:
    history = history_text_from_state(state)
    prompt = runtime.prompt_registry.get_prompt("retrieval_query_rewrite").format(
        query=state["query"],
        history=history,
        file_context=state.get("file_context") or "",
        long_memory=state.get("long_term_memory") or "",
    )
    response = await traced_ainvoke(
        runtime.lite_llm,
        prompt,
        run_name=LlmRunName.QUERY_REWRITE,
        user_id=str(state.get("user_id") or "") or None,
        session_id=str(state.get("session_id") or "") or None,
    )
    raw = response.content if hasattr(response, "content") else str(response)
    return {"rewritten_query": message_content_to_plain_str(raw)}


async def criminal_case_retrieval_subgraph(state: RetrievalRewriteState) -> dict:
    """Run Neo4j + Mongo criminal retrieval LangGraph; output feeds criminal-court RAG."""
    from app.orchestration.agents.criminal_retrieval_langgraph import (
        build_criminal_retrieval_graph,
    )

    app = build_criminal_retrieval_graph()
    q = (state.get("rewritten_query") or state.get("query") or "").strip()
    if not q:
        return {"criminal_case_context": ""}
    result = await app.ainvoke(
        {"question": q},
        langchain_invoke_config(
            LlmRunName.CRIMINAL_CASE_RETRIEVAL,
            user_id=str(state.get("user_id") or "") or None,
            session_id=str(state.get("session_id") or "") or None,
        ),
    )
    return {"criminal_case_context": str(result.get("context_markdown") or "")}


async def retrieve_documents(
    state: RetrievalRewriteState, runtime: OrchestrationService
) -> dict:
    return await retrieve_for_assistant(
        state,
        upload_context=state.get("file_context") or "",
    )


async def evaluate_context(
    state: RetrievalRewriteState, runtime: OrchestrationService
) -> dict:
    if not runtime.web_search_enabled(state):
        return {}

    evaluation = await runtime.web_search.evaluate_context_sufficiency(
        state["query"],
        state.get("retrieval_context") or "",
    )
    return {"context_evaluation_output": evaluation}


async def web_search_fallback(
    state: RetrievalRewriteState, runtime: OrchestrationService
) -> dict:
    if not runtime.web_search_enabled(state):
        return {}

    evaluation = state.get("context_evaluation_output") or {}
    if not runtime.web_search.should_run_web_search(
        assistant_name=state.get("assistant_name"),
        deep_research=bool(state.get("deep_research")),
        evaluation=evaluation,
    ):
        return {}

    query = state.get("rewritten_query") or state["query"]
    web_output = await runtime.web_search.run_web_search_fallback(query)
    merged = runtime.web_search.merge_web_results(
        state.get("retrieval_context") or "",
        web_output,
    )
    return {
        "web_search_output": web_output,
        "retrieval_context": merged,
    }


async def generate_final_answer(
    state: RetrievalRewriteState, runtime: OrchestrationService
) -> dict:
    answer_chunks: list[str] = []
    async for item in stream_final_answer(state, runtime):
        if isinstance(item, str):
            answer_chunks.append(item)
    answer = "".join(answer_chunks).strip()
    return {
        "final_answer": answer,
        "messages": [AIMessage(content=answer)],
        # Drop bulky field from checkpointed thread state (used only mid-graph).
        "retrieval_context": "",
        "criminal_case_context": "",
    }


async def stream_final_answer(
    state: RetrievalRewriteState,
    runtime: OrchestrationService,
    *,
    usage_holder: dict[str, Any] | None = None,
) -> AsyncGenerator[str | dict[str, Any], None]:
    """Stream final answer via LangGraph ``create_agent`` + Redis checkpointer (thread memory)."""
    from app.orchestration.llms import LangChain
    from app.orchestration.utils import agent_session_thread_id

    system_prompt, user_prompt = build_final_answer_messages(state, runtime)
    thread_id = agent_session_thread_id(
        str(state.get("user_id") or ""),
        str(state.get("session_id") or ""),
    )
    save_orchestration_llm_context_json(
        state=dict(state),
        system_prompt=system_prompt,
        user_prompt=user_prompt,
        thread_id=thread_id,
    )
    assistant = (
        state.get("selected_assistant")
        or state.get("assistant_name")
        or state.get("collection_name")
        or "main"
    )
    lc = LangChain(checkpointer=runtime.checkpointer)
    async for item in lc.astream_turn(
        thread_id=thread_id,
        query=user_prompt.strip(),
        system_prompt=system_prompt,
        assistant_name=str(assistant),
        user_id_for_logs=str(state.get("user_id") or ""),
        session_id=str(state.get("session_id") or "") or None,
    ):
        if (
            usage_holder is not None
            and isinstance(item, dict)
            and item.get("type") == "_generation_meta"
        ):
            meta = item.get("meta")
            if isinstance(meta, dict) and isinstance(meta.get("token_usage"), dict):
                usage_holder["token_usage"] = meta["token_usage"]
        yield item


def build_final_answer_messages(
    state: RetrievalRewriteState,
    runtime: OrchestrationService,
) -> tuple[str, str]:
    assistant = resolve_assistant(state)
    prompt_template = state.get("answer_prompt_template")
    if prompt_template is not None:
        system_prompt = prompt_template.template
    else:
        system_prompt = runtime.prompt_registry.get_assistant_prompt(
            AssistantConfig.validate_assistant_or_default(assistant)
        ).template

    retrieval_context = state.get("retrieval_context") or ""
    user_prompt = (
        f"User question:\n{state['query']}\n\n"
        f"Rewritten retrieval query:\n{state.get('rewritten_query', '')}\n\n"
        f"Retrieved legal context:\n"
        f"{retrieval_context if retrieval_context else '[NO CONTEXT FOUND]'}\n\n"
        f"User uploaded file context:\n{state.get('file_context') or ''}\n\n"
    )
    return system_prompt, user_prompt

