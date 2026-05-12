"""
Context-retrieval stage orchestrated with LangGraph (``StateGraph``).

Nodes: strategy → fetch → END; optional evaluate → web search when enabled.
LLM steps use OpenAI JSON mode via ``retrieval_structured_llm``; web uses Tavily.
Conversation history for the final LLM is not assembled here;
see ``build_pipeline_thread_chat_history`` in ``runtime``.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import replace
from typing import Any

from app.agents.common.state import AgentRequestContext
from app.agents.court import CourtAgent
from app.agents.pipeline.retrieval_structured_llm import (
    context_evaluation_llm,
    retrieval_strategy_llm,
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

_STRATEGY_FILE_EXCERPT_MAX_TOKENS = 2_000
_STRATEGY_FILE_CONTEXT_MAX_TOKENS = 8_000


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
        """Run retrieval subgraph as a LangGraph state machine (strategy → … → eval → [web])."""
        self._get_agent_fn = get_agent
        try:
            initial = state.model_dump(mode="python")
            final = await self._compiled_retrieval_graph().ainvoke(initial)
            merged = ChatPipelineState.model_validate({**initial, **final})
            for name in ChatPipelineState.model_fields:
                setattr(state, name, getattr(merged, name))
        finally:
            self._get_agent_fn = None

    async def _strategy_or_route_phase(self, state: ChatPipelineState) -> None:
        locked = state.locked_assistant
        canonical = (
            AssistantConfig.validate_assistant_or_default(locked) if locked else None
        )
        if canonical == "court":
            await self._emit_progress(
                ProgressEventType.RETRIEVAL_STRATEGY,
                "completed",
                "Court assistant — routing to specialist",
            )
            await self._set_default_retrieval_strategy(state)
            return

        await self._emit_progress(
            ProgressEventType.RETRIEVAL_STRATEGY,
            "in_progress",
            "Determining best search strategy for your question...",
        )
        try:
            strategy_file_context = await self._strategy_file_context(state)
            strategy_response = await retrieval_strategy_llm(
                state.query,
                file_context=strategy_file_context,
            )
            state.retrieval_output = strategy_response.model_dump()
            state.rewritten_query = strategy_response.query_rewrite or state.query
            state.selected_assistant = strategy_response.assistant
            if locked:
                state.selected_assistant = (
                    AssistantConfig.validate_assistant_or_default(locked)
                )
            state.selected_assistant = normalize_assistant_name_for_registry(
                state.selected_assistant or "main"
            )
            await self._emit_progress(
                ProgressEventType.RETRIEVAL_STRATEGY,
                "completed",
                f"Using {state.selected_assistant} assistant",
            )
        except Exception as error:
            await self._handle_error(state, "Strategy selection", error)
            await self._set_default_retrieval_strategy(state)
            if locked:
                state.selected_assistant = normalize_assistant_name_for_registry(
                    AssistantConfig.validate_assistant_or_default(locked)
                )
            await self._emit_progress(
                ProgressEventType.RETRIEVAL_STRATEGY,
                "completed",
                "Using default search strategy",
            )

    async def _fetch_documents_phase(self, state: ChatPipelineState) -> None:
        await self._emit_progress(
            ProgressEventType.DOCUMENT_RETRIEVAL,
            "in_progress",
            "Searching legal document database...",
        )
        try:
            retrieval_output = state.retrieval_output or {}
            state.rewritten_query = retrieval_output.get("query_rewrite", state.query)
            upload_context = await self._fetch_upload_context(state)
            locked = state.locked_assistant
            canonical = (
                AssistantConfig.validate_assistant_or_default(locked)
                if locked
                else None
            )

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

    async def _fetch_lexuz_corpus(self, state: ChatPipelineState) -> None:
        retrieval_output = state.retrieval_output or {}
        strategy = retrieval_output.get("strategy", "hybrid")
        if strategy == "specific":
            strategy = "hybrid"
        assistant = state.selected_assistant or "main"
        state.rewritten_query = retrieval_output.get("query_rewrite", state.query)
        collection_name = self._get_collection_name(assistant, strategy)
        state.retrieval_docs = await self.retrieval.retrieve_formatted_corpus(
            query=state.rewritten_query or state.query,
            top_k=settings.TOP_K,
            alpha=settings.ALPHA,
            search_type=strategy,
            collection_name=collection_name,
        )

    async def _strategy_file_context(self, state: ChatPipelineState) -> str:
        """Bounded upload/project excerpts for retrieval-strategy query rewriting."""
        try:
            from app.agents.main import MainAgent

            agent = MainAgent()
            req = self._request_from_state(state)
            file_ids = await agent._resolve_turn_file_ids(req) or []
            project_id = await agent._resolve_turn_project_id(req)
            if not file_ids and not project_id:
                return ""

            parts: list[str] = []
            seen: set[str] = set()

            def append_record(record: dict | None) -> None:
                if not record:
                    return
                file_id = str(record.get("_id") or record.get("file_id") or "")
                if not file_id or file_id in seen:
                    return
                seen.add(file_id)
                meta = record.get("file_metadata") or {}
                file_name = meta.get("file_name") or file_id
                excerpt = (record.get("ocr_result") or "").strip()
                if excerpt:
                    if count_tokens(excerpt) > _STRATEGY_FILE_EXCERPT_MAX_TOKENS:
                        excerpt = truncate_to_token_limit(
                            excerpt, _STRATEGY_FILE_EXCERPT_MAX_TOKENS
                        )
                    parts.append(f"File: {file_name}\n{excerpt}")

            for file_id in file_ids:
                append_record(await self.history_service.get_file_by_id(file_id))

            if project_id:
                project_files = await self.history_service.get_files_by_project(
                    project_id,
                    limit=5,
                )
                for record in project_files:
                    append_record(record)

            if not parts:
                return ""

            combined = "\n\n".join(parts)
            if count_tokens(combined) > _STRATEGY_FILE_CONTEXT_MAX_TOKENS:
                combined = truncate_to_token_limit(
                    combined, _STRATEGY_FILE_CONTEXT_MAX_TOKENS
                )
            return combined
        except Exception as exc:
            logger.warning(
                "[pipeline] strategy file context failed: %s", exc, exc_info=True
            )
            return ""

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
                state.rewritten_query or state.query,
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
        retrieval_output = state.retrieval_output or {}
        state.rewritten_query = retrieval_output.get("query_rewrite", state.query)
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
        retrieval_output = state.retrieval_output or {}
        state.rewritten_query = retrieval_output.get("query_rewrite", state.query)
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
        retrieval_output = state.retrieval_output or {}
        state.rewritten_query = retrieval_output.get("query_rewrite", state.query)
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

    async def _perform_web_search(self, state: ChatPipelineState) -> None:
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

    def _get_collection_name(self, assistant: str, strategy: str) -> str:
        try:
            collection_name = AssistantConfig.get_collection_name(assistant)
        except ValueError:
            collection_name = settings.MILVUS_MAIN_NAME
        return collection_name

    async def _set_default_retrieval_strategy(self, state: ChatPipelineState) -> None:
        default_response = RetrievalStrategyResponse(
            strategy="hybrid",
            query_rewrite=state.query,
            assistant="main",
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
