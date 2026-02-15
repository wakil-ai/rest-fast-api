from collections.abc import AsyncGenerator
from dataclasses import dataclass
from typing import Any

from langchain_core.prompts import PromptTemplate

from app.assistants import (
    BaseAssistant,
    MainAssistant,
    MamuriyAssistant,
    ShartnomaAssistant,
    SoliqAssistant,
)
from app.chains.prompts_registry import PromptRegistry
from app.core.config import settings
from app.core.logger import logger
from app.llms import LLM, ChatGPT, Claude, Gemini, Novita
from app.retrieval.retrieval_service import RetrievalService
from app.services.chat_history_service import ChatHistoryService
from app.services.memory_service import ChatMemoryService
from app.utils.tokens import count_tokens, truncate_to_token_limit


@dataclass
class GenerationContext:
    """All context needed for response generation."""

    context: str
    system_prompt: str
    chat_history: str
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
        "soliq": SoliqAssistant,
        "mamuriy_sud": MamuriyAssistant,
        "shartnoma": ShartnomaAssistant,
    }

    def __init__(self):
        # Assistants (lazy-cached per name)
        self._assistant_cache: dict[str, BaseAssistant] = {}

        # Generic retrieval (for agentic RAG flow only)
        self.retrieval = RetrievalService()

        self.memory = ChatMemoryService()
        self.history_service = ChatHistoryService()
        self.fallback_llm = ChatGPT()
        self.prompts_registry = PromptRegistry()

    def _get_assistant(self, name: str) -> BaseAssistant:
        """Get or create an assistant instance by name."""
        if name not in self._assistant_cache:
            cls = self.ASSISTANT_REGISTRY.get(name, MainAssistant)
            self._assistant_cache[name] = cls()
        return self._assistant_cache[name]

    #  Public API
    async def generate_answer(
        self,
        user_id: str,
        query: str,
        chat_history: list | None = None,
        stream: bool = settings.STREAM,
        file_ids: list[str] | None = None,
        assistant: str = "main",
        model_name: str | None = None,
    ) -> str | AsyncGenerator[str, None] | tuple[str, dict[str, Any]]:
        """
        Main entry point to generate a response (streaming or not).
        """
        try:
            ctx = await self._prepare_generation_context(
                user_id=user_id,
                query=query,
                chat_history=chat_history,
                file_ids=file_ids,
                assistant=assistant,
            )

            llm = self._select_llm(model_name)

            if stream:
                return self._generate_streaming(llm, query, ctx, assistant)
            else:
                return await self._generate_non_streaming(llm, query, ctx, assistant)

        except Exception as e:
            logger.error(f"Generation failed: {e}", exc_info=True)
            return self._create_error_response(stream)

    #  LLM Selection
    def _select_llm(self, model_name: str | None) -> LLM:
        """Factory method: select LLM based on model name prefix."""
        if not model_name:
            return self.fallback_llm

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
        chat_history: list | None,
        file_ids: list[str] | None,
        assistant: str,
    ) -> GenerationContext:
        """Gather all pieces needed for generation: files, memory, retrieval, prompt."""
        # 1. File context (OCR results)
        file_context = await self._collect_file_context(file_ids)

        # 2. Chat history + memory
        history_formatted = self._format_chat_history(chat_history)
        memory_text = await self.memory.search_memory(user_id, query)
        full_history_text = "\n".join(filter(None, [history_formatted, memory_text]))

        # 3. Retrieval — each assistant handles its own classification / filtering
        retrieved_context, attachments, template_override = (
            await self._retrieve_relevant_context(
                query=query,
                file_context=file_context,
                chat_history=history_formatted,
                assistant=assistant,
            )
        )

        # 4. Prompt template (assistant may override via intent classification)
        template = template_override or self.prompts_registry.get_assistant_prompt(
            assistant
        )

        # 5. Truncate if necessary
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

        # 6. Final system prompt
        system_prompt = self._build_system_prompt(
            template, retrieved_context, full_history_text
        )

        logger.debug(f"[SYSTEM PROMPT]\n{system_prompt}")

        return GenerationContext(
            context=retrieved_context,
            system_prompt=system_prompt,
            chat_history=history_formatted,
            attachments=attachments,
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
    def _format_chat_history(self, chat_history: list | None) -> str:
        if not chat_history:
            return ""

        recent = chat_history[-settings.CHAT_HISTORY_LIMIT :]
        if not recent:
            return ""

        lines = ["Previous Conversation History:"]
        for i, entry in enumerate(recent, 1):
            lines.append(f"{i}. User: {entry.question}")
            lines.append(f"   Assistant: {entry.answer}")
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
    ) -> AsyncGenerator[str, None]:
        async def gen():
            buffer = []
            try:
                async for chunk in self._stream_from_llm(llm, query, ctx.system_prompt):
                    buffer.append(chunk)
                    yield chunk
            except Exception:
                logger.warning("Primary LLM streaming failed → fallback", exc_info=True)
                try:
                    async for chunk in self._stream_from_llm(
                        self.fallback_llm, query, ctx.system_prompt
                    ):
                        yield chunk
                except Exception:
                    logger.error("Fallback LLM also failed", exc_info=True)
                    yield "Sorry, I couldn't generate an answer right now."

            full = "".join(buffer)
            logger.debug(f"[STREAM FINAL]\n{full}")

            # Special case: shartnoma → send attachments separately
            if assistant == "shartnoma" and ctx.attachments:
                yield {"type": "attachments", "attachments": ctx.attachments}

        return gen()

    async def _generate_non_streaming(
        self,
        llm: LLM,
        query: str,
        ctx: GenerationContext,
        assistant: str,
    ) -> tuple[str, dict[str, Any]]:
        try:
            raw = await self._get_response(llm, query, ctx.system_prompt)
        except Exception:
            logger.warning("Primary LLM failed → fallback", exc_info=True)
            try:
                raw = await self._get_response(
                    self.fallback_llm, query, ctx.system_prompt
                )
            except Exception:
                logger.error("Fallback LLM failed", exc_info=True)
                raise

        cleaned = self._clean_text(raw)
        logger.info(
            f"LLM response: {cleaned[:300]}{'...' if len(cleaned) > 300 else ''}"
        )

        meta = {
            "attachments": ctx.attachments if assistant == "shartnoma" else [],
        }

        if settings.DEVELOPMENT_MODE:
            meta["retrieved_contents"] = ctx.context

        return cleaned, meta

    async def _stream_from_llm(
        self, llm: LLM, user_prompt: str, system_prompt: str
    ) -> AsyncGenerator[str, None]:
        gen = await llm.generate_response(
            user_prompt=user_prompt,
            system_prompt=system_prompt,
            stream=True,
        )
        async for chunk in gen:
            if chunk:
                yield self._clean_text(chunk)

    async def _get_response(
        self, llm: LLM, user_prompt: str, system_prompt: str
    ) -> str:
        return await llm.generate_response(
            user_prompt=user_prompt,
            system_prompt=system_prompt,
            stream=False,
        )

    #  Utilities
    @staticmethod
    def _clean_text(text: str) -> str:
        """Replace fancy punctuation with standard characters."""
        return text.replace("【", "[").replace("】", "]")

    def _create_error_response(self, stream: bool) -> Any:
        msg = "Sorry, I couldn't generate an answer at the moment."

        if not stream:
            return msg

        async def err_gen():
            yield msg

        return err_gen()
