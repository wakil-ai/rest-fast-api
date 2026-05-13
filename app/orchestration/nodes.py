from __future__ import annotations

from typing import TYPE_CHECKING, Any

from langchain_core.messages import AIMessage, HumanMessage, SystemMessage

from app.core.assistants import AssistantConfig
from app.orchestration.retrieval import (
    resolve_assistant,
    retrieve_for_assistant,
)
from app.orchestration.state import RetrievalRewriteState
from app.orchestration.utils import load_turn_file_context

if TYPE_CHECKING:
    from app.orchestration.service import OrchestrationService


def _llm_content_to_str(content: Any) -> str:
    """Normalize Gemini / LangChain message content to a plain string."""
    if content is None:
        return ""
    if isinstance(content, str):
        return content.strip()
    if isinstance(content, list):
        parts: list[str] = []
        for block in content:
            if isinstance(block, str):
                parts.append(block)
            elif isinstance(block, dict) and block.get("type") == "text":
                parts.append(str(block.get("text", "")))
            else:
                parts.append(str(block))
        return "".join(parts).strip()
    return str(content).strip()


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

    history = _history_text(state)
    domain, _prompt, intent = await runtime.intent_classifier.classify_intent(
        state["query"],
        chat_history=history,
        file_context=state.get("file_context") or "",
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

    history = _history_text(state)
    decision = await runtime.court_classifier.route_query(
        state["query"],
        chat_history=history,
        file_context=state.get("file_context") or "",
    )
    return {
        "selected_assistant": decision.assistant_name or "administrative_court",
        "court_route_tag": decision.court_route_tag,
    }


async def rewrite_query(
    state: RetrievalRewriteState, runtime: OrchestrationService
) -> dict:
    history = state.get("messages", [])[-10:]
    prompt = runtime.prompt_registry.get_prompt("retrieval_query_rewrite").format(
        query=state["query"],
        history=history,
        file_context=state.get("file_context") or "",
        long_memory=state.get("long_term_memory") or "",
    )
    response = await runtime.rewrite_llm.ainvoke(prompt)
    raw = response.content if hasattr(response, "content") else str(response)
    return {"rewritten_query": _llm_content_to_str(raw)}


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

    response = await runtime.generation_llm.ainvoke(
        [
            SystemMessage(content=system_prompt),
            HumanMessage(content=user_prompt),
        ]
    )
    answer = _llm_content_to_str(
        response.content if hasattr(response, "content") else response
    )
    return {
        "final_answer": answer,
        "messages": [AIMessage(content=answer)],
        # Drop bulky field from checkpointed thread state (used only mid-graph).
        "retrieval_context": "",
    }


def _history_text(state: RetrievalRewriteState) -> str:
    messages = state.get("messages") or []
    return "\n".join(
        f"{message.type}: {message.content}"
        for message in messages
        if getattr(message, "content", None)
    )
