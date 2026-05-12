"""
Context-retrieval stage orchestrated with LangGraph (``StateGraph``).

Nodes: resolve query → fetch → END; deep-research assistants may run evaluate → web search.
LLM steps use OpenAI JSON mode via ``retrieval_structured_llm``; web uses Tavily.
Conversation history for the final LLM is not assembled here;
see ``build_pipeline_thread_chat_history`` in ``runtime``.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import replace
from time import perf_counter
from typing import Any

from app.agents.common.runtime import (
    agent_session_thread_id,
    build_retrieval_thread_chat_history,
)
from app.agents.common.state import AgentRequestContext
from app.agents.court import CourtAgent
from app.agents.pipeline.retrieval_follow_up import resolve_follow_up_selection
from app.agents.pipeline.retrieval_structured_llm import (
    context_evaluation_llm,
    retrieval_query_rewrite_llm,
    web_search_tavily,
)
from app.agents.pipeline.schemas import (
    ChatPipelineState,
    ProgressEventType,
    RetrievalStrategyResponse,
    WebSearchResponse,
    normalize_assistant_name_for_registry,
)
from app.core.assistants import AssistantConfig
from app.core.config import settings
from app.core.dependencies import (
    get_chat_history_service,
    get_retrieval_service,
)
from app.core.logger import logger
from app.utils.streaming import format_progress_event
from app.utils.tokens import count_tokens, truncate_to_token_limit


class ContextRetrievalRunner:
    """Assembles all context before the final reasoning LLM (no corpus summarization)."""

    def __init__(
        self,
        *,
        progress_callback: Callable[[dict[str, Any]], Awaitable[None]] | None = None,
    ) -> None:
        self.progress_callback = progress_callback
        self.retrieval = get_retrieval_service()
        self.history_service = get_chat_history_service()
        self._get_agent_fn: Callable[[str], Any] | None = None
        self._retrieval_graph: Any | None = None

    def _build_retrieval_graph(self) -> Any:
        from app.agents.pipeline.retrieval_graph import compile_context_retrieval_graph

        return compile_context_retrieval_graph(self)

    def _compiled_retrieval_graph(self) -> Any:
        if self._retrieval_graph is None:
            self._retrieval_graph = self._build_retrieval_graph()
        return self._retrieval_graph

    def _require_get_agent(self) -> Callable[[str], Any]:
        if self._get_agent_fn is None:
            raise RuntimeError(
                "get_agent unset; ContextRetrievalRunner.run() must set it."
            )
        return self._get_agent_fn

    @staticmethod
    def _pipeline_web_search_enabled(assistant_name: str | None) -> bool:
        return AssistantConfig.is_web_search_enabled(assistant_name)

    def _record_timing(self, state: ChatPipelineState, step: str, started: float) -> None:
        elapsed_ms = (perf_counter() - started) * 1000.0
        state.retrieval_timing_ms[step] = round(elapsed_ms, 2)
        logger.info("[pipeline] retrieval %s %.1fms", step, elapsed_ms)

    def _apply_resolved_queries(
        self,
        state: ChatPipelineState,
        *,
        resolved_query: str,
        rewritten_query: str,
        follow_up_resolved: bool = False,
    ) -> None:
        state.resolved_query = resolved_query
        state.rewritten_query = rewritten_query
        memory_output: dict[str, Any] = {"resolved_query": resolved_query}
        if follow_up_resolved:
            memory_output["follow_up_resolved"] = True
        state.memory_output = memory_output
        state.retrieval_output = RetrievalStrategyResponse(
            strategy="hybrid",
            query_rewrite=rewritten_query,
            assistant=state.selected_assistant or "main",
        ).model_dump()

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

    async def run(
        self,
        state: ChatPipelineState,
        get_agent: Callable[[str], Any],
    ) -> None:
        """Run retrieval subgraph (resolve query → fetch → [eval → web])."""
        self._get_agent_fn = get_agent
        try:
            initial = state.model_dump(mode="python")
            final = await self._compiled_retrieval_graph().ainvoke(initial)
            merged = ChatPipelineState.model_validate({**initial, **final})
            for name in ChatPipelineState.model_fields:
                setattr(state, name, getattr(merged, name))
        finally:
            self._get_agent_fn = None

    async def _resolve_query_phase(self, state: ChatPipelineState) -> None:
        started = perf_counter()
        await self._emit_progress(
            ProgressEventType.RETRIEVAL_STRATEGY,
            "in_progress",
            "Preparing your question for search...",
        )
        try:
            thread_id = state.langgraph_thread_id
            if not thread_id and state.session_id and state.user_id:
                thread_id = agent_session_thread_id(state.user_id, state.session_id)
                state.langgraph_thread_id = thread_id

            locked = state.locked_assistant
            canonical = (
                AssistantConfig.validate_assistant_or_default(locked) if locked else None
            )
            state.selected_assistant = normalize_assistant_name_for_registry(
                canonical or state.selected_assistant or "main"
            )

            chat_history = ""
            if state.session_id and state.user_id:
                chat_history = await build_retrieval_thread_chat_history(
                    state.user_id, state.session_id
                )
            state.memory_docs = chat_history or None

            await self._set_default_retrieval_strategy(state)

            if canonical == "court":
                await self._emit_progress(
                    ProgressEventType.RETRIEVAL_STRATEGY,
                    "completed",
                    "Court assistant — routing to specialist",
                )
                return

            if chat_history.strip():
                follow_up_query = resolve_follow_up_selection(
                    state.query, chat_history
                )
                if follow_up_query:
                    self._apply_resolved_queries(
                        state,
                        resolved_query=follow_up_query,
                        rewritten_query=follow_up_query,
                        follow_up_resolved=True,
                    )
                else:
                    rewrite = await retrieval_query_rewrite_llm(
                        state.query,
                        chat_history=chat_history,
                        thread_id=thread_id,
                    )
                    self._apply_resolved_queries(
                        state,
                        resolved_query=rewrite.resolved_query,
                        rewritten_query=rewrite.query_rewrite,
                    )
            else:
                state.resolved_query = state.query
                state.rewritten_query = state.query

            await self._emit_progress(
                ProgressEventType.RETRIEVAL_STRATEGY,
                "completed",
                "Search query ready",
            )
        except Exception as error:
            await self._handle_error(state, "Query resolution", error)
            await self._set_default_retrieval_strategy(state)
            state.resolved_query = state.query
            state.rewritten_query = state.query
            await self._emit_progress(
                ProgressEventType.RETRIEVAL_STRATEGY,
                "completed",
                "Using default search query",
            )
        finally:
            self._record_timing(state, "resolve_query", started)

    async def _fetch_documents_phase(self, state: ChatPipelineState) -> None:
        started = perf_counter()
        await self._emit_progress(
            ProgressEventType.DOCUMENT_RETRIEVAL,
            "in_progress",
            "Searching legal document database...",
        )
        try:
            upload_started = perf_counter()
            upload_context = await self._fetch_upload_context(state)
            self._record_timing(state, "upload_context", upload_started)

            chat_history = (state.memory_docs or "").strip()
            thread_id = state.langgraph_thread_id
            follow_up_locked = bool(
                (state.memory_output or {}).get("follow_up_resolved")
            )
            if upload_context.strip() and not follow_up_locked:
                rewrite_started = perf_counter()
                rewrite = await retrieval_query_rewrite_llm(
                    state.resolved_query or state.query,
                    chat_history=chat_history,
                    thread_id=thread_id,
                    file_context=upload_context,
                )
                self._apply_resolved_queries(
                    state,
                    resolved_query=rewrite.resolved_query,
                    rewritten_query=rewrite.query_rewrite,
                )
                self._record_timing(state, "query_rewrite_upload", rewrite_started)

            locked = state.locked_assistant
            canonical = (
                AssistantConfig.validate_assistant_or_default(locked)
                if locked
                else None
            )

            corpus_started = perf_counter()
            if canonical == "court":
                await self._fetch_court_routed(state, upload_context)
            elif canonical == "contract_analyzer":
                await self._fetch_contract(state, upload_context)
            elif canonical == "tax":
                await self._fetch_tax(state, upload_context)
            elif canonical in (
                "administrative_court",
                "criminal_court",
                "civil_court",
                "economic_court",
            ):
                await self._fetch_direct_court_specialist(
                    state, canonical, upload_context
                )
            else:
                await self._fetch_lexuz_corpus(state)
                if upload_context:
                    state.retrieval_docs = (
                        (state.retrieval_docs or "").rstrip() + "\n\n" + upload_context
                    )
            self._record_timing(state, "corpus_fetch", corpus_started)

            state.retrieval_docs = self._limit_retrieval_docs(
                state.retrieval_docs or ""
            )

            await self._emit_progress(
                ProgressEventType.DOCUMENT_RETRIEVAL,
                "completed",
                "Retrieved relevant documents from knowledge base",
            )
        except Exception as error:
            await self._handle_error(state, "Document retrieval", error)
            state.retrieval_docs = ""
        finally:
            self._record_timing(state, "fetch_documents", started)

    async def _fetch_lexuz_corpus(self, state: ChatPipelineState) -> None:
        retrieval_output = state.retrieval_output or {}
        assistant = state.selected_assistant or "main"
        state.rewritten_query = (
            retrieval_output.get("query_rewrite")
            or state.rewritten_query
            or state.resolved_query
            or state.query
        )
        collection_name = self._get_collection_name(assistant)
        state.retrieval_docs = await self.retrieval.retrieve_formatted_corpus(
            query=state.rewritten_query or state.query,
            top_k=settings.TOP_K,
            alpha=settings.ALPHA,
            search_type="hybrid",
            collection_name=collection_name,
        )

    async def _fetch_upload_context(self, state: ChatPipelineState) -> str:
        """One upload/project retrieval per turn (Milvus + OCR as needed)."""
        try:
            from app.agents.main import MainAgent

            agent = MainAgent()
            req = self._request_from_state(state)
            file_ids = await agent._resolve_turn_file_ids(req)
            project_id = await agent._resolve_turn_project_id(req)
            if not file_ids and not project_id:
                return ""
            block = await agent._collect_file_context(
                file_ids,
                state.user_id,
                state.rewritten_query or state.resolved_query or state.query,
                project_id=project_id,
            )
            return agent._limit_file_context_for_llm(block)
        except Exception as exc:
            logger.warning(
                "[pipeline] upload context retrieval failed: %s", exc, exc_info=True
            )
            return ""

    @staticmethod
    def _limit_retrieval_docs(context: str) -> str:
        text = (context or "").strip()
        if not text:
            return ""
        limit = settings.RETRIEVAL_CONTEXT_TOKEN_LIMIT
        token_count = count_tokens(text)
        if token_count <= limit:
            return text
        logger.warning(
            "Retrieval context exceeds token limit (%s > %s). Truncating.",
            token_count,
            limit,
        )
        return truncate_to_token_limit(text, limit)

    async def _fetch_tax(self, state: ChatPipelineState, upload_context: str) -> None:
        state.rewritten_query = (
            state.rewritten_query or state.resolved_query or state.query
        )
        agent = self._require_get_agent()("tax")
        r = await agent.retrieve(
            query=state.rewritten_query,
            file_context=upload_context or "",
            chat_history="",
        )
        state.retrieval_docs = r.context or ""

    async def _fetch_contract(
        self, state: ChatPipelineState, upload_context: str
    ) -> None:
        state.rewritten_query = (
            state.rewritten_query or state.resolved_query or state.query
        )
        agent = self._require_get_agent()("contract_analyzer")
        r = await agent.retrieve(
            query=state.rewritten_query,
            file_context=upload_context or "",
            chat_history="",
        )
        state.retrieval_docs = r.context or ""
        state.answer_prompt_template = r.prompt_template
        state.classified_legal_intent = r.classified_legal_intent
        if r.attachments:
            state.attachments = list(state.attachments or []) + list(r.attachments)

    async def _fetch_direct_court_specialist(
        self,
        state: ChatPipelineState,
        canonical: str,
        upload_context: str,
    ) -> None:
        state.rewritten_query = (
            state.rewritten_query or state.resolved_query or state.query
        )
        agent = self._require_get_agent()(canonical)
        kwargs: dict[str, Any] = {}
        if canonical == "administrative_court":
            kwargs["court_route_tag"] = getattr(
                agent,
                "DEFAULT_COURT_ROUTE_TAG",
                "administrative_general_admin_litigation",
            )
        r = await agent.retrieve(
            query=state.rewritten_query or "",
            file_context=upload_context or "",
            chat_history="",
            **kwargs,
        )
        state.retrieval_docs = r.context or ""
        state.answer_prompt_template = r.prompt_template

    async def _fetch_court_routed(
        self, state: ChatPipelineState, upload_context: str
    ) -> None:
        req = self._request_from_state(state)
        if upload_context:
            req = replace(req, preloaded_file_context=upload_context)
        court = CourtAgent()
        sub_agent, routed_request, court_route_tag = await court._route(req)
        state.selected_assistant = routed_request.assistant
        state.court_route_tag = court_route_tag
        state.rewritten_query = state.resolved_query or state.query

        if hasattr(sub_agent, "_forced_court_route_tag"):
            sub_agent._forced_court_route_tag = court_route_tag
        try:
            fc = routed_request.preloaded_file_context or ""
            r = await sub_agent.retrieve(
                query=state.rewritten_query,
                file_context=fc,
                chat_history="",
                court_route_tag=court_route_tag,
            )
        finally:
            if hasattr(sub_agent, "_forced_court_route_tag"):
                sub_agent._forced_court_route_tag = None

        state.retrieval_docs = r.context or ""
        state.answer_prompt_template = r.prompt_template

    def _request_from_state(self, state: ChatPipelineState) -> AgentRequestContext:
        return AgentRequestContext(
            user_id=state.user_id,
            session_id=state.session_id,
            message_id=state.message_id,
            query=state.query,
            assistant=state.selected_assistant or "main",
            file_ids=state.file_ids,
            project_id=state.project_id,
            stream=False,
        )

    async def _evaluate_context_sufficiency(self, state: ChatPipelineState) -> bool:
        started = perf_counter()
        await self._emit_progress(
            ProgressEventType.CONTEXT_EVALUATION,
            "in_progress",
            "Evaluating document relevance and quality...",
        )
        try:
            evaluation_response = await context_evaluation_llm(
                state.query,
                state.retrieval_docs or "No context retrieved.",
            )
            state.context_evaluation_output = evaluation_response.model_dump()
            ok = bool(evaluation_response.is_sufficient)
            if not ok:
                await self._emit_progress(
                    ProgressEventType.CONTEXT_EVALUATION,
                    "completed",
                    "Context insufficient — augmenting with web search",
                )
            else:
                await self._emit_progress(
                    ProgressEventType.CONTEXT_EVALUATION,
                    "completed",
                    "Context sufficient for answering",
                )
            return ok
        except Exception as error:
            await self._handle_error(state, "Context evaluation", error)
            state.context_evaluation_output = {
                "is_sufficient": True,
                "reasoning": "Evaluation failed, proceeding with available context",
                "missing_info": "",
            }
            await self._emit_progress(
                ProgressEventType.CONTEXT_EVALUATION,
                "completed",
                "Proceeding with available information",
            )
            return True
        finally:
            self._record_timing(state, "context_evaluation", started)

    async def _perform_web_search(self, state: ChatPipelineState) -> None:
        started = perf_counter()
        await self._emit_progress(
            ProgressEventType.WEB_SEARCH,
            "in_progress",
            "Searching the web for additional information...",
        )
        try:
            web_response = await self._execute_web_search(state)
            await self._merge_web_documents(state, web_response)
            message = await self._format_web_search_message(web_response)
            await self._emit_progress(
                ProgressEventType.WEB_SEARCH, "completed", message
            )
        except Exception as error:
            await self._handle_error(state, "Web search", error)
            await self._emit_progress(
                ProgressEventType.WEB_SEARCH,
                "completed",
                "Proceeding without web results",
            )
        finally:
            self._record_timing(state, "web_search", started)

    def _get_collection_name(self, assistant: str) -> str:
        try:
            collection_name = AssistantConfig.get_collection_name(assistant)
        except ValueError:
            collection_name = settings.MILVUS_MAIN_NAME
        return collection_name

    async def _set_default_retrieval_strategy(self, state: ChatPipelineState) -> None:
        default_response = RetrievalStrategyResponse(
            strategy="hybrid",
            query_rewrite=state.rewritten_query or state.query,
            assistant=state.selected_assistant or "main",
        )
        state.retrieval_output = default_response.model_dump()
        if not state.selected_assistant:
            state.selected_assistant = "main"

    async def _execute_web_search(self, state: ChatPipelineState) -> WebSearchResponse:
        web_response = await web_search_tavily(state.rewritten_query or state.query)
        state.web_search_output = web_response.model_dump()
        return web_response

    async def _merge_web_documents(
        self, state: ChatPipelineState, web_response: WebSearchResponse
    ) -> None:
        combined_docs = state.retrieval_docs or ""
        combined_docs += "\n\n--- Web Search Results ---\n"
        for doc in web_response.docs:
            combined_docs += f"\nSource: {doc.url}\n"
            if doc.title:
                combined_docs += f"Title: {doc.title}\n"
            combined_docs += f"{doc.content}\n"
        state.retrieval_docs = combined_docs

    async def _format_web_search_message(self, web_response: WebSearchResponse) -> str:
        docs = web_response.docs
        if not docs:
            return "No relevant web documents found"
        sources = [
            f"Title: {doc.title}\n Url:({doc.url})\n Content: ({doc.content})\n\n"
            for doc in docs
        ]
        return f"Found {len(docs)} web document(s) as additional sources: {'; '.join(sources)}"

    async def _handle_error(
        self, state: ChatPipelineState, operation: str, error: Exception
    ) -> None:
        logger.error(f"✗ {operation} failed: {error}", exc_info=True)
        state.errors.append(f"{operation} error: {str(error)}")
