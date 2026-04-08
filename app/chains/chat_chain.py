from collections.abc import AsyncGenerator
from dataclasses import dataclass
from typing import Any

from langchain_core.prompts import PromptTemplate

from app.assistants import (
    AdministrativeCourtAssistant,
    BaseAssistant,
    CivilCourtAssistant,
    ContractAnalyzerAssistant,
    CriminalCourtAssistant,
    EconomicCourtAssistant,
    MainAssistant,
    TaxAssistant,
)
from app.chains.court_classifier import CourtRoutingDecision
from app.core.assistants import AssistantConfig
from app.core.config import settings
from app.core.dependencies import (
    get_chat_history_service,
    get_court_classifier,
    get_fallback_llm,
    get_memory_service,
    get_prompt_registry,
    get_retrieval_service,
)
from app.core.logger import logger
from app.llms import LLM, ChatGPT, Claude, Gemini, Novita
from app.retrieval.embedding_manager import get_instruction
from app.utils.tokens import count_tokens, truncate_to_token_limit


@dataclass
class GenerationContext:
    """All context needed for response generation."""

    user_id: str
    context: str
    system_prompt: str
    chat_history: str
    assistant_name: str
    attachments: list[dict[str, Any]] | None = None


class ChatChain:
    """
    Main RAG (Retrieval-Augmented Generation) chain with automatic fallback.

    Responsibilities:
    - Retrieve relevant documents
    - Prepare context + prompts
    - Generate answer using selected LLM (with fallback)
    """

    # Assistant registry — maps assistant name → class
    ASSISTANT_REGISTRY: dict[str, type[BaseAssistant]] = {
        "main": MainAssistant,
        "umumiy": MainAssistant,
        "tax": TaxAssistant,
        "administrative_court": AdministrativeCourtAssistant,
        "contract_analyzer": ContractAnalyzerAssistant,
        "criminal_court": CriminalCourtAssistant,
        "economic_court": EconomicCourtAssistant,
        "civil_court": CivilCourtAssistant,
    }

    def __init__(self):
        # Assistants (lazy-cached per name)
        self._assistant_cache: dict[str, BaseAssistant] = {}

        # Generic retrieval (for agentic RAG flow only)
        self.retrieval = get_retrieval_service()

        self.memory = get_memory_service()
        self.history_service = get_chat_history_service()
        self.court_classifier = get_court_classifier()
        self.fallback_llm = get_fallback_llm()
        self.prompts_registry = get_prompt_registry()

    async def _collect_generation_meta(
        self,
        *,
        llm: LLM,
        query: str,
        system_prompt: str,
        answer: str,
    ) -> dict[str, Any]:
        """Collect best-effort generation metadata for persistence."""
        meta: dict[str, Any] = {}

        model = getattr(llm, "model", None)
        if model:
            meta["model"] = model

        try:
            input_token = await llm.count_tokens(query)
            context_token = await llm.count_tokens(system_prompt)
            output_token = await llm.count_tokens(answer)

            embedding_text = ""
            if query:
                embedding_text = get_instruction(query)
            embedding_input_token = await llm.count_tokens(embedding_text)

            meta["token_usage"] = {
                "input_token": input_token,
                "context_token": context_token,
                "output_token": output_token,
                "embedding_input_token": embedding_input_token,
            }
        except Exception as e:
            logger.warning(f"Token stats collection failed: {e}", exc_info=True)

        return meta

    def _get_assistant(self, name: str) -> BaseAssistant:
        """Get or create an assistant instance by name."""
        canonical = AssistantConfig.validate_assistant_or_default(name)
        if canonical not in self._assistant_cache:
            cls = self.ASSISTANT_REGISTRY.get(canonical, MainAssistant)
            assistant_cls: Any = cls
            self._assistant_cache[canonical] = assistant_cls()
        return self._assistant_cache[canonical]

    #  Public API
    async def generate_answer(
        self,
        user_id: str,
        session_id: str,
        query: str,
        stream: bool = settings.STREAM,
        file_ids: list[str] | None = None,
        assistant: str = "main",
    ) -> str | AsyncGenerator[Any, None] | tuple[str, dict[str, Any]]:
        """
        Main entry point to generate a response (streaming or not).
        """
        try:
            assistant = AssistantConfig.validate_assistant_or_default(assistant)

            file_context = await self._collect_file_context(file_ids)
            history_formatted = await self._get_session_history_text(session_id)
            routing_decision = await self._resolve_court_routing(
                assistant=assistant,
                query=query,
                chat_history=history_formatted,
                file_context=file_context,
            )

            if routing_decision.out_of_scope_message:
                return self._create_static_response(
                    routing_decision.out_of_scope_message,
                    stream,
                )

            ctx = await self._prepare_generation_context(
                user_id=user_id,
                query=query,
                assistant=routing_decision.assistant_name or assistant,
                file_context=file_context,
                history_formatted=history_formatted,
            )

            llm = self._select_llm()

            if stream:
                return self._generate_streaming(llm, query, ctx, ctx.assistant_name)
            else:
                return await self._generate_non_streaming(llm, query, ctx)

        except Exception as e:
            logger.error(f"Generation failed: {e}", exc_info=True)
            return self._create_error_response(stream)

    #  LLM Selection
    def _select_llm(self) -> LLM:
        """Factory method: select the configured default LLM."""
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

        logger.warning(f"Unknown model '{model_name}' → using fallback")
        return self.fallback_llm

    #  Context Preparation
    async def _prepare_generation_context(
        self,
        user_id: str,
        query: str,
        assistant: str,
        file_context: str,
        history_formatted: str,
    ) -> GenerationContext:
        """Gather all pieces needed for generation: files, memory, retrieval, prompt."""
        # 1. Chat history + memory
        memory_text = await self.memory.search_memory(user_id, query)
        full_history_text = "\n".join(
            part for part in [history_formatted, memory_text] if part
        )

        # 2. Retrieval — each assistant handles its own classification / filtering
        (
            retrieved_context,
            attachments,
            template_override,
        ) = await self._retrieve_relevant_context(
            query=query,
            file_context=file_context,
            chat_history=history_formatted,
            assistant=assistant,
        )

        # 3. Prompt template (assistant may override via intent classification)
        template = template_override or self.prompts_registry.get_assistant_prompt(
            assistant
        )

        # 4. Truncate if necessary
        total_tokens = count_tokens(retrieved_context + full_history_text)
        if total_tokens > settings.MAX_RETRIEVAL_DOCS_TOKEN_LIMIT:
            logger.warning(
                f"Context + history exceeds token limit ({total_tokens} > "
                f"{settings.MAX_RETRIEVAL_DOCS_TOKEN_LIMIT}). Truncating context."
            )
            max_ctx_tokens = settings.MAX_RETRIEVAL_DOCS_TOKEN_LIMIT - count_tokens(
                full_history_text
            )
            retrieved_context = truncate_to_token_limit(
                retrieved_context, max_ctx_tokens
            )

        # 5. Final system prompt
        system_prompt = self._build_system_prompt(
            template, retrieved_context, full_history_text
        )

        logger.debug(f"[SYSTEM PROMPT]\n{system_prompt}")

        return GenerationContext(
            user_id=user_id,
            context=retrieved_context,
            system_prompt=system_prompt,
            chat_history=history_formatted,
            assistant_name=assistant,
            attachments=attachments,
        )

    async def _resolve_court_routing(
        self,
        assistant: str,
        query: str,
        chat_history: str,
        file_context: str,
    ) -> CourtRoutingDecision:
        if assistant != "court":
            return CourtRoutingDecision(assistant_name=assistant)

        return await self.court_classifier.route_query(
            query=query,
            chat_history=chat_history,
            uploaded_file_context=file_context,
        )

    async def _collect_file_context(self, file_ids: list[str] | None) -> str:
        if not file_ids:
            return ""

        parts = []
        for fid in file_ids:
            file = await self.history_service.get_file_by_id(fid)
            if file and file.get("ocr_result"):
                parts.append(f"\n\n## USER FILE CONTEXT\n{file['ocr_result']}")

        return "".join(parts)

    async def _retrieve_relevant_context(
        self,
        query: str,
        file_context: str,
        chat_history: str,
        assistant: str,
    ) -> tuple[str, list[dict[str, Any]], Any]:
        """
        Delegate retrieval to the assistant's own retrieve() method.

        Each assistant encapsulates its own search strategy, formatting,
        classification, and (optionally) multi-collection merging.

        Returns (context, attachments, prompt_template_or_None).
        """
        assistant_instance = self._get_assistant(assistant)
        result = await assistant_instance.retrieve(
            query=query,
            file_context=file_context,
            chat_history=chat_history,
        )
        return result.context, result.attachments, result.prompt_template

    #  Prompt & History Formatting
    async def _get_session_history_text(self, session_id: str) -> str:
        if not session_id:
            return ""

        messages = await self.history_service.get_recent_messages(
            session_id=session_id,
            limit=settings.CHAT_HISTORY_LIMIT,
        )
        if not messages:
            return ""

        entries: list[tuple[str, str]] = []
        for i, entry in enumerate(messages, 1):
            content = entry.get("content") or {}
            question = content.get("query")
            answer = content.get("response")
            if not question or not answer:
                continue

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

    #  Generation (streaming & non-streaming)
    def _generate_streaming(
        self,
        llm: LLM,
        query: str,
        ctx: GenerationContext,
        assistant: str,
    ) -> AsyncGenerator[Any, None]:
        async def gen() -> AsyncGenerator[Any, None]:
            answer_chunks: list[str] = []
            active_llm = llm
            try:
                async for chunk in self._stream_from_llm(llm, query, ctx.system_prompt):
                    if isinstance(chunk, str):
                        answer_chunks.append(chunk)
                    yield chunk
            except Exception:
                logger.warning("Primary LLM streaming failed → fallback", exc_info=True)
                try:
                    active_llm = self.fallback_llm
                    async for chunk in self._stream_from_llm(
                        active_llm, query, ctx.system_prompt
                    ):
                        if isinstance(chunk, str):
                            answer_chunks.append(chunk)
                        yield chunk
                except Exception:
                    logger.error("Fallback LLM also failed", exc_info=True)
                    yield "Sorry, I couldn't generate an answer right now."

            full = "".join(answer_chunks)
            logger.debug(f"[STREAM FINAL]\n{full}")

            meta = await self._collect_generation_meta(
                llm=active_llm,
                query=query,
                system_prompt=ctx.system_prompt,
                answer=full,
            )

            meta["attachments"] = ctx.attachments or []

            # Send attachments if the assistant provided any
            if ctx.attachments:
                yield {"type": "attachments", "attachments": ctx.attachments}

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
            raw = await self._get_response(llm, query, ctx.system_prompt)
        except Exception:
            logger.warning("Primary LLM failed → fallback", exc_info=True)
            try:
                active_llm = self.fallback_llm
                raw = await self._get_response(active_llm, query, ctx.system_prompt)
            except Exception:
                logger.error("Fallback LLM failed", exc_info=True)
                raise

        cleaned = self._clean_text(raw)
        logger.info(
            f"LLM response: {cleaned[:300]}{'...' if len(cleaned) > 300 else ''}"
        )

        meta = await self._collect_generation_meta(
            llm=active_llm,
            query=query,
            system_prompt=ctx.system_prompt,
            answer=cleaned,
        )

        meta["attachments"] = ctx.attachments or []

        return cleaned, meta

    async def _stream_from_llm(
        self, llm: LLM, user_prompt: str, system_prompt: str
    ) -> AsyncGenerator[Any, None]:
        gen = await llm.generate_response(
            user_prompt=user_prompt,
            system_prompt=system_prompt,
            stream=True,
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
        self, llm: LLM, user_prompt: str, system_prompt: str
    ) -> str:
        response = await llm.generate_response(
            user_prompt=user_prompt,
            system_prompt=system_prompt,
            stream=False,
        )

        if not isinstance(response, str):
            raise TypeError("Expected non-streaming response from LLM.")

        return response

    #  Utilities
    @staticmethod
    def _clean_text(text: str) -> str:
        """Replace fancy punctuation with standard characters."""
        return text.replace("【", "[").replace("】", "]")

    def _create_error_response(self, stream: bool) -> Any:
        msg = "Sorry, I couldn't generate an answer at the moment."

        return self._create_static_response(msg, stream)

    @staticmethod
    def _create_static_response(message: str, stream: bool) -> Any:
        if not stream:
            return message

        async def err_gen():
            yield message

        return err_gen()
