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
from app.orchestration.utils import load_turn_file_context

if TYPE_CHECKING:
    from app.orchestration.service import OrchestrationService


def _query_for_classification(state: RetrievalRewriteState) -> str:
    """Intent / court routing run after rewrite — use expanded retrieval query."""
    return (state.get("rewritten_query") or state.get("query") or "").strip()


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

    domain, _prompt, intent = await runtime.intent_classifier.classify_intent(
        _query_for_classification(state),
        chat_history="",
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

    decision = await runtime.court_classifier.route_query(
        _query_for_classification(state),
        chat_history="",
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
    from app.core.dependencies import get_criminal_mode_classifier
    from app.orchestration.agents.criminal_retrieval_langgraph import (
        build_criminal_retrieval_graph,
    )

    app = build_criminal_retrieval_graph()
    q = (state.get("rewritten_query") or state.get("query") or "").strip()
    if not q:
        return {"criminal_case_context": "", "criminal_modes": [1]}

    user_id = str(state.get("user_id") or "") or None
    session_id = str(state.get("session_id") or "") or None

    classifier = get_criminal_mode_classifier()
    decision = await classifier.classify(
        q,
        chat_history=history_text_from_state(state),
        file_context=(state.get("file_context") or ""),
        user_id=user_id,
        session_id=session_id,
    )

    result = await app.ainvoke(
        {"question": q},
        langchain_invoke_config(
            LlmRunName.CRIMINAL_CASE_RETRIEVAL,
            user_id=user_id,
            session_id=session_id,
        ),
    )
    return {
        "criminal_case_context": str(result.get("context_markdown") or ""),
        "criminal_modes": list(decision.modes),
    }


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


_FINAL_CHAT_HISTORY_HINT = (
    "Earlier turns in this chat are available in the conversation thread. "
    "Ground legal analysis only in the retrieved legal context below—not in the user turn."
)


def _get_final_prompt_template(
    state: RetrievalRewriteState,
    runtime: OrchestrationService,
    assistant: str,
):
    """Route-specific court prompts when set; otherwise the assistant default template."""
    answer_tpl = state.get("answer_prompt_template")
    if answer_tpl is not None and hasattr(answer_tpl, "format"):
        return answer_tpl
    return runtime.prompt_registry.get_assistant_prompt(
        AssistantConfig.validate_assistant_or_default(assistant)
    )


def _resolve_final_system_prompt(
    state: RetrievalRewriteState,
    runtime: OrchestrationService,
    assistant: str,
) -> str:
    """Inject retrieval into system prompt; criminal court uses MODE-composed v2 parts."""
    retrieval_context = (state.get("retrieval_context") or "").strip()
    criminal_cases = (state.get("criminal_case_context") or "").strip()
    context_value = retrieval_context or "[NO RETRIEVED LEGAL CONTEXT]"

    if assistant == "criminal_court":
        from app.orchestration.prompts import (
            DEFAULT_MODES,
            get_criminal_prompt_composer,
        )

        modes = state.get("criminal_modes") or list(DEFAULT_MODES)
        composer = get_criminal_prompt_composer()
        composer.log_composition(
            list(modes),
            query_preview=(state.get("query") or "")[:120],
        )
        return composer.format_prompt(
            list(modes),
            retrieved_cases=criminal_cases
            or "(No criminal graph matches for this query.)",
            context=context_value,
            chat_history=_FINAL_CHAT_HISTORY_HINT,
        )

    tpl = _get_final_prompt_template(state, runtime, assistant)
    format_kwargs: dict[str, str] = {
        "context": context_value,
        "chat_history": _FINAL_CHAT_HISTORY_HINT,
    }
    input_vars = getattr(tpl, "input_variables", None) or []
    if "retrieved_cases" in input_vars:
        format_kwargs["retrieved_cases"] = (
            criminal_cases or "(No criminal graph matches for this query.)"
        )
    return tpl.format(**format_kwargs)


def _uploaded_file_context_only(state: RetrievalRewriteState) -> str:
    """User-uploaded file text only (excludes project workspace Milvus block)."""
    combined = (state.get("file_context") or "").strip()
    if not combined:
        return ""
    project = (state.get("project_related_context") or "").strip()
    if not project:
        return combined
    if combined.startswith(project):
        remainder = combined[len(project) :].lstrip("\n")
        return remainder.strip()
    return combined


def build_final_answer_messages(
    state: RetrievalRewriteState,
    runtime: OrchestrationService,
) -> tuple[str, str]:
    assistant = resolve_assistant(state)
    system_prompt = _resolve_final_system_prompt(state, runtime, assistant)

    user_sections = [
        f"User question:\n{state['query']}",
        f"Rewritten retrieval query:\n{state.get('rewritten_query') or ''}",
    ]
    uploads = _uploaded_file_context_only(state)
    if uploads:
        user_sections.append(f"User uploaded files:\n{uploads}")
    project_files = (state.get("project_related_context") or "").strip()
    if project_files:
        user_sections.append(f"Project files:\n{project_files}")

    user_prompt = "\n\n".join(user_sections) + "\n\n"
    return system_prompt, user_prompt

