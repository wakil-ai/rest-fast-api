from collections.abc import AsyncGenerator
from dataclasses import dataclass
from typing import Any

from app.core.assistants import AssistantConfig
from app.core.config import settings
from app.core.logger import logger
from app.llms.base import LLM
from app.llms.claude import Claude
from app.llms.gpt import ChatGPT
from app.llms.novita import Novita
from app.retrieval.retrieval_service import RetrievalService
from app.services.chat_history_service import ChatHistoryService
from app.services.memory_service import ChatMemoryService
from app.utils.tokens import count_tokens, truncate_to_token_limit


@dataclass
class GenerationContext:
    """Encapsulates all context needed for response generation."""

    context: str
    system_prompt: str
    chat_history: str
    attachments: list[dict[str, Any]]


class ChatChain:
    """
    Main chain for retrieval-augmented generation (RAG):
    1. Retrieve relevant documents from vector DB
    2. Build context and prompts
    3. Generate answer using selected LLM with automatic fallback
    """

    def __init__(self):
        self.retrieval_service = RetrievalService()
        self.memory_service = ChatMemoryService()
        self.chat_history_service = ChatHistoryService()
        self.fallback_llm = ChatGPT()

    # Public API
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
        Generate response using RAG with automatic fallback.

        Args:
            user_id: User identifier for memory retrieval
            query: User's question
            chat_history: Previous conversation messages
            stream: Whether to stream the response
            file_ids: Optional list of file IDs to use as context
            collection_name: Vector DB collection to query
            model_name: Specific model to use

        Returns:
            String response, async generator, or tuple with debug data
        """
        try:
            # Prepare context
            gen_context = await self._prepare_generation_context(
                user_id=user_id,
                query=query,
                chat_history=chat_history,
                file_ids=file_ids,
                assistant=assistant,
            )

            # Select LLM
            llm = self._get_llm(model_name) if model_name else self.fallback_llm

            # Generate response
            if stream:
                return self._generate_stream(llm, query, gen_context, assistant)
            else:
                return await self._generate_non_stream(
                    llm, query, gen_context, assistant
                )

        except Exception as e:
            logger.error(f"[ChatChain] Generation failed: {e}", exc_info=True)
            return self._create_error_response(stream, {})

    # LLM Selection
    def _get_llm(self, model_name: str) -> LLM:
        """Factory method to get LLM instance based on model name."""
        logger.debug(f"[ChatChain] Selecting LLM for model: {model_name}")

        model_mapping = {
            "gemma-": Novita,
            "gpt-oss-": Novita,
            "gpt-": ChatGPT,
            "claude-": Claude,
        }

        for prefix, llm_class in model_mapping.items():
            if model_name.startswith(prefix):
                return llm_class(model_name=model_name)

        logger.warning(f"[ChatChain] Unknown model '{model_name}', using fallback")
        return self.fallback_llm

    # Context Preparation
    async def _prepare_generation_context(
        self,
        user_id: str,
        query: str,
        chat_history: list | None,
        file_ids: list[str] | None,
        assistant: str,
    ) -> GenerationContext:
        """Prepare all context needed for generation."""
        # Get collection name with assistant config
        collection_name = AssistantConfig.get_collection_name(assistant)

        # Collect file context if any
        file_context = ""
        if file_ids:
            for file_id in file_ids:
                file = self.chat_history_service.get_file_by_id(file_id)
                if file:
                    file_context += f"\n\nFile Context\n{file['ocr_result']}"

        context, attachments = await self._retrieve_context(
            query=query,
            collection_name=collection_name,
            file_context=file_context,
        )

        # Build chat history with memory
        chat_history_text = self._format_chat_history(chat_history)
        memory_text = await self.memory_service.search_memory(user_id, query)
        history_text = "\n".join(filter(None, [chat_history_text, memory_text]))

        # Ensure context token limit
        total_tokens = count_tokens(context + history_text)
        if total_tokens > settings.MAX_RETRIEVAL_DOCS_TOKEN_LIMIT:
            logger.warning(
                f"[ChatChain] Retrieved context exceeds token limit of "
                f"{settings.MAX_RETRIEVAL_DOCS_TOKEN_LIMIT} tokens. Truncating context."
            )
            context = truncate_to_token_limit(
                context,
                settings.MAX_RETRIEVAL_DOCS_TOKEN_LIMIT - count_tokens(history_text),
            )

        logger.debug(f"[ChatChain] Total tokens in context + history: {total_tokens}")

        # Build system prompt
        system_prompt = self._build_system_prompt(
            assistant,
            context,
            history_text,
        )

        logger.debug(f"[ChatChain] System Prompt: {system_prompt}")

        return GenerationContext(
            context=context,
            system_prompt=system_prompt,
            chat_history=chat_history_text,
            attachments=attachments,
        )

    async def _retrieve_context(
        self, query: str, collection_name: str, file_context: str | None
    ) -> tuple[str, list[dict[str, Any]]]:
        """Retrieve context from vector DB."""
        context, attachments = await self.retrieval_service.retrieve_context(
            query=query,
            top_k=settings.TOP_K,
            collection_name=collection_name,
            file_context=file_context if file_context else None,
        )

        if file_context:
            context = f"{context}\nFile Content:\n{file_context}"

        return context, attachments

    def _format_chat_history(self, chat_history: list | None) -> str:
        """Format recent chat history for inclusion in prompt."""
        if not chat_history:
            return ""

        recent = chat_history[-settings.CHAT_HISTORY_LIMIT :]
        lines = ["Previous Conversation History:"]

        for idx, entry in enumerate(recent, start=1):
            lines.append(f"{idx}. User: {entry.question}")
            lines.append(f"   Assistant: {entry.answer}")

        lines.append("Use the above conversation to maintain context.")
        return "\n".join(lines)

    def _build_system_prompt(
        self,
        assistant: str,
        context: str,
        chat_history: str,
    ) -> str:
        """Build system prompt from template and context."""
        template = AssistantConfig.get_assistant_prompt_template(assistant)
        return template.format(context=context, chat_history=chat_history)

    # Response Generation
    async def _generate_stream(
        self, llm: LLM, query: str, gen_context: GenerationContext, assistant: str
    ) -> AsyncGenerator[str, None]:
        """Generate streaming response with fallback."""
        buffer: list[str] = []

        try:
            async for chunk in self._stream_from_llm(
                llm, query, gen_context.system_prompt
            ):
                buffer.append(chunk)
                yield chunk
        except Exception:
            logger.warning(
                "[ChatChain] Primary LLM failed, using fallback", exc_info=True
            )
            try:
                async for chunk in self._stream_from_llm(
                    self.fallback_llm, query, gen_context.system_prompt
                ):
                    yield chunk
            except Exception:
                logger.error("[ChatChain] Fallback LLM failed", exc_info=True)
                yield "Sorry, I couldn't generate an answer at the moment."

        # Log full response for debugging
        full_response = "".join(buffer)
        logger.debug(
            f"[ChatChain][FINAL_STREAM_RESPONSE]\n {full_response}",
        )

        # For shartnoma assistant, emit docx links at the end (separate from the text stream).
        if assistant == "shartnoma" and gen_context.attachments:
            yield {"type": "attachments", "attachments": gen_context.attachments}

    async def _generate_non_stream(
        self, llm: LLM, query: str, gen_context: GenerationContext, assistant: str
    ) -> tuple[str, dict[str, Any]]:
        """Generate non-streaming response with fallback."""
        try:
            response = await self._get_response(llm, query, gen_context.system_prompt)
        except Exception:
            logger.warning(
                "[ChatChain] Primary LLM failed, using fallback", exc_info=True
            )
            try:
                response = await self._get_response(
                    self.fallback_llm, query, gen_context.system_prompt
                )
            except Exception:
                logger.error("[ChatChain] Fallback failed", exc_info=True)
                raise

        response = self._clean_text(response)
        logger.info(f"[DEBUG] LLM full response: {response}")

        meta: dict[str, Any] = {
            "attachments": gen_context.attachments if assistant == "shartnoma" else [],
        }

        if settings.DEVELOPMENT_MODE:
            meta["retrieved_contents"] = gen_context.context

        return response, meta

    async def _stream_from_llm(
        self, llm: LLM, user_prompt: str, system_prompt: str
    ) -> AsyncGenerator[str, None]:
        """Stream cleaned chunks from LLM."""
        response_gen = await llm.generate_response(
            user_prompt=user_prompt,
            system_prompt=system_prompt,
            stream=True,
        )

        async for chunk in response_gen:
            if chunk:
                yield self._clean_text(chunk)

    async def _get_response(
        self, llm: LLM, user_prompt: str, system_prompt: str
    ) -> str:
        """Get non-streaming response from LLM."""
        return await llm.generate_response(
            user_prompt=user_prompt,
            system_prompt=system_prompt,
            stream=False,
        )

    # Utilities
    @staticmethod
    def _clean_text(text: str) -> str:
        """Replace special punctuation with standard equivalents."""
        replacements = {"【": "[", "】": "]"}
        for old, new in replacements.items():
            text = text.replace(old, new)
        return text

    def _create_error_response(self, stream: bool) -> str | AsyncGenerator[str, None]:
        """Create error response in appropriate format."""
        error_msg = "Sorry, I couldn't generate an answer at the moment."

        if stream:

            async def error_gen():
                yield error_msg

            return error_gen()

        return error_msg
