from __future__ import annotations

import asyncio
import uuid
from collections.abc import AsyncGenerator
from typing import Any

from langchain_core.prompts import PromptTemplate
from langchain_core.tools import tool

from app.orchestration.utils import (
    AgentRequestContext,
    AgentState,
    GenerationContext,
    build_web_search_tool,
    collect_file_and_project_context,
    resolve_turn_file_ids,
    resolve_turn_project_id,
)
from app.core.config import settings
from app.core.langfuse_tracing import LlmRunName
from app.core.dependencies import (
    get_chat_history_service,
    get_context_formatter,
    get_db_manager,
    get_embedding_manager,
    get_fallback_llm,
    get_memory_service,
    get_project_service,
    get_prompt_registry,
    get_retrieval_service,
    get_storage_service,
)
from app.core.logger import logger
from app.orchestration.providers import LLM
from app.orchestration.providers import ChatGPT, Claude, Gemini, Novita
from app.orchestration.llms import LangChain
from app.models.retrieval_models import RetrievalConfig, RetrievalResult
from app.retrieval.embedding_manager import get_instruction
from app.utils.contract_docx import contract_text_to_docx_bytes


class BaseAgent:
    """Base class for assistant agents that own one complete chat turn."""

    assistant_name = "main"

    def __init__(
        self,
        collection_name: str,
        top_k: int = settings.TOP_K,
        *,
        assistant_name: str | None = None,
    ):
        self.collection_name = collection_name
        self.top_k = top_k
        if assistant_name:
            self.assistant_name = assistant_name
        self._user_file_context_prefix = (
            "# USER FILE CONTEXT:\n"
            "Note: This is the context of the user uploaded files."
        )

    @property
    def db(self):
        return get_db_manager()

    @property
    def embedder(self):
        return get_embedding_manager()

    @property
    def formatter(self):
        return get_context_formatter()

    @property
    def retrieval_service(self):
        return get_retrieval_service()

    @property
    def history_service(self):
        return get_chat_history_service()

    @property
    def memory_service(self):
        return get_memory_service()

    @property
    def prompt_registry(self):
        return get_prompt_registry()

    @property
    def fallback_llm(self):
        return get_fallback_llm()

    def build_prompt(self, state: AgentState) -> None:
        """Build the tool-first system prompt, embedding any preloaded file context."""
        template = self.prompt_registry.get_assistant_prompt(state.resolved_assistant)
        state.system_prompt = self._build_system_prompt(
            template,
            self._compose_context_section(state),
            "Use the `get_chat_history` tool to access recent conversation history.",
        )
        logger.debug(
            f"[{self.__class__.__name__} SYSTEM PROMPT]\n{state.system_prompt}"
        )

    def _compose_context_section(self, state: AgentState) -> str:
        """Combine per-agent tool guidance with any eagerly preloaded uploaded-file context."""
        parts: list[str] = [self._context_guidance_text()]
        if state.file_context:
            parts.append(
                "The user has uploaded one or more files. Their content is provided below "
                "for direct reference — analyze it as part of this turn instead of asking "
                "for clarification about what to analyze. Use the `get_uploaded_file_context` "
                "tool only when you need refined keyword searches over the same files."
                f"\n\n{state.file_context}"
            )
        return "\n\n".join(parts)

    def _context_guidance_text(self) -> str:
        """Override per-agent to describe available domain tools."""
        return (
            "Use `search_legal_corpus` to retrieve relevant Uzbek legal texts and statutes. "
            "Use `get_chat_history` for conversation context and `search_memory` for user preferences. "
            "Uploaded file content (when present) is provided directly below; only call "
            "`get_uploaded_file_context` for refined keyword searches across the same files. "
            "Do not invent sources not returned by the search tools."
        )

    async def _preload_file_context(self, request: AgentRequestContext) -> str:
        """Eagerly fetch uploaded-file / project context once per turn.

        Reuses ``request.preloaded_file_context`` when a parent agent (e.g. ``CourtAgent``)
        already loaded it. Returns ``""`` when nothing is uploaded or fetch fails.
        """
        if request.preloaded_file_context is not None:
            return request.preloaded_file_context
        effective_file_ids = await resolve_turn_file_ids(request)
        effective_project_id = await resolve_turn_project_id(request)
        if effective_file_ids and not request.file_ids:
            # Keep tools bound to the inherited attachments for this turn.
            request.file_ids = effective_file_ids
        if effective_project_id and not request.project_id:
            request.project_id = effective_project_id
        if not (effective_file_ids or effective_project_id):
            request.preloaded_file_context = ""
            return ""
        try:
            ctx = await self._collect_file_context(
                effective_file_ids,
                request.user_id,
                request.query,
                project_id=effective_project_id,
            )
        except Exception as exc:
            logger.warning(
                f"[{self.__class__.__name__}] file-context preload failed: {exc}",
                exc_info=True,
            )
            ctx = ""
        request.preloaded_file_context = ctx
        return ctx

    async def _resolve_turn_file_ids(
        self, request: AgentRequestContext
    ) -> list[str] | None:
        return await resolve_turn_file_ids(request)

    async def _resolve_turn_project_id(
        self, request: AgentRequestContext
    ) -> str | None:
        return await resolve_turn_project_id(request)

    def build_tools(self, request: AgentRequestContext, state: AgentState) -> list[Any]:
        """Return the full tool list for this agent turn."""
        tools: list[Any] = []
        tools.extend(self._build_domain_tools(request, state))
        tools.extend(self._build_common_tools(request))
        web_tool = build_web_search_tool()
        if web_tool is not None:
            tools.append(web_tool)
        return tools

    def _build_common_tools(self, request: AgentRequestContext) -> list[Any]:
        """History, memory, and file-context tools bound to this request."""
        session_id = request.session_id
        user_id = request.user_id
        file_ids = request.file_ids
        project_id = request.project_id

        @tool
        async def get_chat_history() -> str:
            """Retrieve recent conversation history to maintain context and consistency."""
            return await self._get_session_history_text(session_id)

        @tool
        async def search_memory(query: str) -> str:
            """Search the user's personal memory for relevant preferences and prior context."""
            try:
                return await self.memory_service.search_memory(user_id, query) or ""
            except Exception as exc:
                logger.warning(f"search_memory failed: {exc}", exc_info=True)
                return ""

        tools: list[Any] = [get_chat_history, search_memory]

        if file_ids or project_id:

            @tool
            async def get_uploaded_file_context(query: str) -> str:
                """Search user-uploaded files and project documents for content relevant to the query."""
                return await self._collect_file_context(
                    file_ids, user_id, query, project_id=project_id
                )

            tools.append(get_uploaded_file_context)

        return tools

    def _build_domain_tools(
        self, request: AgentRequestContext, state: AgentState
    ) -> list[Any]:
        """Override in subclasses to provide domain-specific retrieval tools."""

        @tool
        async def search_legal_corpus(query: str) -> str:
            """Search Uzbekistan legal corpus (Lexuz) for relevant statutes and codified norms."""
            try:
                result = await self.retrieve(
                    query=query, file_context="", chat_history=""
                )
                return result.context or "No relevant legal documents found."
            except Exception as exc:
                logger.warning(f"search_legal_corpus failed: {exc}", exc_info=True)
                return "Search failed; answer from general reasoning where appropriate."

        return [search_legal_corpus]

    async def prepare_state(self, request: AgentRequestContext) -> AgentState:
        """Kept for callers that need an AgentState without tool-building (e.g. CourtAgent routing)."""
        state = AgentState(request=request, resolved_assistant=self.assistant_name)
        state.file_context = await self._preload_file_context(request)
        self.build_prompt(state)
        project_instructions = await self._load_project_instructions(request)
        if project_instructions:
            state.system_prompt = (
                f"{state.system_prompt.rstrip()}\n\n"
                "## Project-specific instructions (user-defined)\n"
                f"{project_instructions}"
            )
        return state

    async def _load_project_instructions(
        self, request: AgentRequestContext
    ) -> str | None:
        project_id = await resolve_turn_project_id(request)
        if not project_id:
            return None
        try:
            project = await get_project_service().get_project(
                project_id, request.user_id
            )
        except Exception as exc:
            logger.warning(
                f"[{self.__class__.__name__}] failed to load project instructions: {exc}",
                exc_info=True,
            )
            return None
        return get_project_service().extract_instructions(project)

    async def attach_outputs(
        self, answer: str, state: AgentState
    ) -> list[dict[str, Any]]:
        return list(state.attachments or [])

    def to_generation_context(self, state: AgentState) -> GenerationContext:
        from app.orchestration.utils import agent_session_thread_id

        tid = (
            agent_session_thread_id(state.request.user_id, state.request.session_id)
            if state.request.session_id
            else None
        )
        return GenerationContext(
            user_id=state.request.user_id,
            session_id=state.request.session_id,
            context=state.retrieval_context,
            system_prompt=state.system_prompt,
            chat_history=state.chat_history,
            assistant_name=state.resolved_assistant,
            attachments=state.attachments,
            classified_legal_intent=state.classified_legal_intent,
            court_route_tag=state.court_route_tag,
            langgraph_thread_id=tid,
        )

    async def retrieve(
        self,
        query: str,
        file_context: str = "",
        *,
        chat_history: str = "",
        **kwargs,
    ) -> RetrievalResult:
        try:
            config = self._build_config()
            raw_docs = await self.asearch(query, config)
            result = await self.format_results(raw_docs)
            if file_context and result.context:
                result.context += f"\n\n\n{file_context}"
            elif file_context:
                result.context = file_context
            return result
        except Exception as e:
            logger.error(
                f"[{self.__class__.__name__}] Retrieval failed: {e}", exc_info=True
            )
            return self._error_result()

    def search(self, query: str, config: RetrievalConfig) -> list[dict[str, Any]]:
        embedding = self.embedder.embed_query(query)
        return self.db.search_hybrid(
            dense_vector=embedding,
            text_query=query,
            top_k=config.top_k,
            collection_name=config.collection_name,
            expr=config.filter or "",
        )

    async def asearch(
        self, query: str, config: RetrievalConfig
    ) -> list[dict[str, Any]]:
        try:
            return await asyncio.to_thread(self.search, query, config)
        except Exception:
            if not config.filter:
                raise
            logger.warning(
                f"Filtered Milvus search failed; retrying without filter. "
                f"collection={config.collection_name} filter={config.filter}",
                exc_info=True,
            )
            fallback_config = RetrievalConfig(
                top_k=config.top_k,
                alpha=config.alpha,
                search_type=config.search_type,
                collection_name=config.collection_name,
                filter="",
            )
            return await asyncio.to_thread(self.search, query, fallback_config)

    async def format_results(self, documents: list[dict[str, Any]]) -> RetrievalResult:
        return await self.formatter.format_results(documents)

    def _build_config(self, **overrides) -> RetrievalConfig:
        defaults = dict(
            top_k=self.top_k,
            collection_name=self.collection_name,
        )
        defaults.update(overrides)
        return RetrievalConfig(**defaults)

    def _error_result(self) -> RetrievalResult:
        if self.collection_name == settings.MILVUS_CONTRACT_ANALYZER:
            return RetrievalResult(context="", attachments=[])
        return RetrievalResult(context="No relevant documents found.", attachments=[])

    def _select_llm(self) -> LLM:
        model_name = settings.DEFAULT_CHAT_MODEL
        mapping = {
            "gemma-": Novita,
            "gpt-oss-": Novita,
            "gpt-": ChatGPT,
            "claude-": Claude,
            "gemini-": Gemini,
        }
        for prefix, cls in mapping.items():
            if model_name.startswith(prefix):
                return cls(model_name=model_name)
        logger.warning(f"Unknown model '{model_name}' -> using fallback")
        return self.fallback_llm

    async def generate_from_context(
        self,
        *,
        query: str,
        ctx: GenerationContext,
        stream: bool,
    ) -> AsyncGenerator[Any, None] | tuple[str, dict[str, Any]]:
        if ctx.langgraph_thread_id:
            return await self._generate_from_langchain_thread(
                query=query, ctx=ctx, stream=stream
            )
        llm = self._select_llm()
        if stream:
            return self._generate_streaming(llm, query, ctx)
        return await self._generate_non_streaming(llm, query, ctx)

    async def _generate_from_langchain_thread(
        self,
        *,
        query: str,
        ctx: GenerationContext,
        stream: bool,
    ) -> AsyncGenerator[Any, None] | tuple[str, dict[str, Any]]:
        from app.orchestration.utils import _get_agent_checkpointer

        lc = LangChain(checkpointer=_get_agent_checkpointer())
        if stream:
            return self._stream_langchain_thread(lc, query=query, ctx=ctx)
        answer, meta = await lc.invoke_turn(
            thread_id=ctx.langgraph_thread_id or "",
            query=query,
            system_prompt=ctx.system_prompt,
            assistant_name=ctx.assistant_name,
            user_id_for_logs=ctx.user_id,
            session_id=ctx.session_id or None,
        )
        meta["attachments"] = list(ctx.attachments or [])
        return answer, meta

    def _stream_langchain_thread(
        self,
        lc: Any,
        *,
        query: str,
        ctx: GenerationContext,
    ) -> AsyncGenerator[Any, None]:
        async def gen() -> AsyncGenerator[Any, None]:
            answer_chunks: list[str] = []
            async for item in lc.astream_turn(
                thread_id=ctx.langgraph_thread_id or "",
                query=query,
                system_prompt=ctx.system_prompt,
                assistant_name=ctx.assistant_name,
                user_id_for_logs=ctx.user_id,
                session_id=ctx.session_id or None,
            ):
                if isinstance(item, str):
                    answer_chunks.append(item)
                    yield item
                elif isinstance(item, dict) and item.get("type") == "think":
                    yield item
                elif isinstance(item, dict) and item.get("type") == "_generation_meta":
                    meta = (
                        item.get("meta") if isinstance(item.get("meta"), dict) else {}
                    ) or {}
                    meta["attachments"] = list(ctx.attachments or [])
                    if meta["attachments"]:
                        yield {
                            "type": "attachments",
                            "attachments": meta["attachments"],
                        }
                    yield {"type": "_generation_meta", "meta": meta}
                    return

            full = "".join(answer_chunks)
            meta = await self._collect_generation_meta(
                llm=lc,
                query=query,
                system_prompt=ctx.system_prompt,
                answer=full,
            )
            meta["attachments"] = list(ctx.attachments or [])
            if meta["attachments"]:
                yield {"type": "attachments", "attachments": meta["attachments"]}
            yield {"type": "_generation_meta", "meta": meta}

        return gen()

    def _generate_streaming(
        self,
        llm: LLM,
        query: str,
        ctx: GenerationContext,
    ) -> AsyncGenerator[Any, None]:
        async def gen() -> AsyncGenerator[Any, None]:
            answer_chunks: list[str] = []
            active_llm = llm
            try:
                async for chunk in self._stream_from_llm(
                    llm, query, ctx.system_prompt, ctx=ctx
                ):
                    if isinstance(chunk, str):
                        answer_chunks.append(chunk)
                    yield chunk
            except Exception:
                logger.warning(
                    "Primary LLM streaming failed -> fallback", exc_info=True
                )
                try:
                    active_llm = self.fallback_llm
                    async for chunk in self._stream_from_llm(
                        active_llm, query, ctx.system_prompt, ctx=ctx
                    ):
                        if isinstance(chunk, str):
                            answer_chunks.append(chunk)
                        yield chunk
                except Exception:
                    logger.error("Fallback LLM also failed", exc_info=True)
                    yield "Sorry, I couldn't generate an answer right now."

            full = "".join(answer_chunks)
            meta = await self._collect_generation_meta(
                llm=active_llm,
                query=query,
                system_prompt=ctx.system_prompt,
                answer=full,
            )
            meta["attachments"] = list(ctx.attachments or [])
            if meta["attachments"]:
                yield {"type": "attachments", "attachments": meta["attachments"]}
            yield {"type": "_generation_meta", "meta": meta}

        return gen()

    async def _generate_non_streaming(
        self,
        llm: LLM,
        query: str,
        ctx: GenerationContext,
    ) -> tuple[str, dict[str, Any]]:
        active_llm = llm
        try:
            raw = await self._get_response(llm, query, ctx.system_prompt, ctx=ctx)
        except Exception:
            logger.warning("Primary LLM failed -> fallback", exc_info=True)
            active_llm = self.fallback_llm
            raw = await self._get_response(
                active_llm, query, ctx.system_prompt, ctx=ctx
            )
        cleaned = self._clean_text(raw)
        meta = await self._collect_generation_meta(
            llm=active_llm,
            query=query,
            system_prompt=ctx.system_prompt,
            answer=cleaned,
        )
        meta["attachments"] = list(ctx.attachments or [])
        return cleaned, meta

    async def _stream_from_llm(
        self,
        llm: LLM,
        user_prompt: str,
        system_prompt: str,
        *,
        ctx: GenerationContext | None = None,
    ) -> AsyncGenerator[Any, None]:
        gen = await llm.generate_response(
            user_prompt=user_prompt,
            system_prompt=system_prompt,
            stream=True,
            run_name=LlmRunName.ASSISTANT_GENERATION,
            user_id=ctx.user_id if ctx else None,
            session_id=(ctx.session_id or None) if ctx else None,
        )
        if isinstance(gen, str):
            raise TypeError("Expected streaming generator from LLM.")
        async for chunk in gen:
            if isinstance(chunk, dict):
                if chunk.get("type") == "think" and chunk.get("chunk"):
                    yield {**chunk, "chunk": self._clean_text(chunk["chunk"])}
                elif chunk:
                    yield chunk
                continue
            if chunk:
                yield self._clean_text(chunk)

    async def _get_response(
        self,
        llm: LLM,
        user_prompt: str,
        system_prompt: str,
        *,
        ctx: GenerationContext | None = None,
    ) -> str:
        response = await llm.generate_response(
            user_prompt=user_prompt,
            system_prompt=system_prompt,
            stream=False,
            run_name=LlmRunName.ASSISTANT_GENERATION,
            user_id=ctx.user_id if ctx else None,
            session_id=(ctx.session_id or None) if ctx else None,
        )
        if not isinstance(response, str):
            raise TypeError("Expected non-streaming response from LLM.")
        return response

    async def _collect_generation_meta(
        self,
        *,
        llm: LLM,
        query: str,
        system_prompt: str,
        answer: str,
    ) -> dict[str, Any]:
        meta: dict[str, Any] = {}
        model = getattr(llm, "model", None)
        if model:
            meta["model"] = model
        try:
            meta["token_usage"] = {
                "input_token": await llm.count_tokens(query),
                "context_token": await llm.count_tokens(system_prompt),
                "output_token": await llm.count_tokens(answer),
                "embedding_input_token": await llm.count_tokens(
                    get_instruction(query) if query else ""
                ),
            }
        except Exception as e:
            logger.warning(f"Token stats collection failed: {e}", exc_info=True)
        return meta

    async def _collect_file_context(
        self,
        file_ids: list[str] | None,
        user_id: str,
        query: str,
        project_id: str | None = None,
    ) -> str:
        _project_context, combined = await collect_file_and_project_context(
            file_ids,
            user_id,
            query,
            project_id=project_id,
        )
        return combined

    async def _get_session_history_text(self, session_id: str) -> str:
        if not session_id:
            return ""
        messages = await self.history_service.get_recent_messages(
            session_id=session_id,
            limit=settings.CHAT_HISTORY_LIMIT,
        )
        entries: list[tuple[str, str]] = []
        for entry in messages or []:
            content = entry.get("content") or {}
            question = content.get("query")
            answer = content.get("response")
            if question and answer:
                entries.append((str(question), str(answer)))
        if not entries:
            return ""
        lines = ["Previous Conversation History:"]
        for i, (question, answer) in enumerate(entries, 1):
            lines.append(f"{i}. User: {question}")
            lines.append(f"   Assistant: {answer}")
        lines.append("Use the above conversation to maintain context and consistency.")
        return "\n".join(lines)

    @staticmethod
    def _build_system_prompt(
        template: PromptTemplate, context: str, chat_history: str
    ) -> str:
        return template.format(context=context, chat_history=chat_history)

    @staticmethod
    def _strip_markdown_code_fence(text: str) -> str:
        t = text.strip()
        if not t.startswith("```"):
            return text.strip()
        lines = t.split("\n")
        if lines and lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].strip() == "```":
            lines = lines[:-1]
        return "\n".join(lines).strip()

    @staticmethod
    def _clean_text(text: str) -> str:
        return text.replace("【", "[").replace("】", "]")

    async def upload_contract_docx(
        self,
        *,
        user_id: str,
        full_answer: str,
        existing_attachments: list[dict[str, Any]] | None,
    ) -> list[dict[str, Any]]:
        merged = list(existing_attachments or [])
        body = self._strip_markdown_code_fence(full_answer)
        if len(body) < 80:
            return merged
        docx_bytes = contract_text_to_docx_bytes(body)
        object_path = f"contract-drafts/{user_id}/{uuid.uuid4().hex}.docx"

        def _upload() -> str:
            storage = get_storage_service()
            return storage.upload_file(
                data=docx_bytes,
                destination_path=object_path,
                content_type=(
                    "application/vnd.openxmlformats-officedocument."
                    "wordprocessingml.document"
                ),
                return_signed_url=True,
            )

        try:
            url = await asyncio.to_thread(_upload)
        except Exception as e:
            logger.warning(
                f"[{self.__class__.__name__}] LLM contract DOCX upload skipped: {e}",
                exc_info=True,
            )
            return merged
        merged.append(
            {
                "name": "shartnoma_loyihasi.docx",
                "url": url,
                "content_type": (
                    "application/vnd.openxmlformats-officedocument."
                    "wordprocessingml.document"
                ),
            }
        )
        return merged
