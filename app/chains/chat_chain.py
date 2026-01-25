from collections.abc import AsyncGenerator
from typing import Any
from dataclasses import dataclass

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


@dataclass
class GenerationContext:
    """Encapsulates all context needed for response generation."""

    context: str
    system_prompt: str
    chat_history: str
    debug_data: dict[str, Any]


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
        project_id: str | None = None,
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
            project_id: Optional project ID for dual-context retrieval
            collection_name: Vector DB collection to query
            model_name: Specific model to use

        Returns:
            String response, async generator, or tuple with debug data
        """
        try:
            collection_name = AssistantConfig.get_collection_name(assistant)

            # Prepare context
            gen_context = await self._prepare_generation_context(
                user_id=user_id,
                query=query,
                chat_history=chat_history,
                file_ids=file_ids,
                project_id=project_id,
                collection_name=collection_name,
            )

            # Select LLM
            llm = self._get_llm(model_name) if model_name else self.fallback_llm

            # Generate response
            if stream:
                return self._generate_stream(llm, query, gen_context)
            else:
                return await self._generate_non_stream(llm, query, gen_context)

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
        project_id: str | None,
        collection_name: str,
    ) -> GenerationContext:
        """Prepare all context needed for generation."""
        debug_data = {"retrieved_contents": []}
        file_context = ""

        if file_ids:
            for file_id in file_ids:
                file = self.chat_history_service.get_file_by_id(file_id)
                if file:
                    file_context += f"\n\nFile: {file['file_metadata']['file_name']}\n{file['ocr_result']}"

        # Retrieve document context
        if project_id:
            context, project_ctx, main_ctx = await self._retrieve_project_context(
                query, project_id, user_id, collection_name
            )
        else:
            context = await self._retrieve_standard_context(
                query, collection_name, file_context
            )
            project_ctx = main_ctx = None

        if file_context:
            context = f"{file_context}\n\n{context}"

        if settings.DEVELOPMENT_MODE:
            debug_data["retrieved_contents"] = context

        # Build chat history with memory
        chat_history_text = self._format_chat_history(chat_history)
        memory_text = await self.memory_service.search_memory(user_id, query)

        if chat_history_text and memory_text:
            chat_history_text = f"{chat_history_text}\n{memory_text}"
        elif memory_text:
            chat_history_text = memory_text

        # Build system prompt
        system_prompt = self._build_system_prompt(
            project_id,
            collection_name,
            context,
            chat_history_text,
            project_ctx,
            main_ctx,
        )

        logger.debug(f"[ChatChain] System Prompt: {system_prompt}")

        return GenerationContext(
            context=context,
            system_prompt=system_prompt,
            chat_history=chat_history_text,
            debug_data=debug_data,
        )

    async def _retrieve_project_context(
        self, query: str, project_id: str, user_id: str, collection_name: str
    ) -> tuple[str, str, str]:
        """Retrieve context from both project files and general knowledge."""
        project_context = await self.retrieval_service.retrieve_project_context(
            query=query,
            project_id=project_id,
            user_id=user_id,
            top_k=settings.TOP_K // 2,
        )

        main_context = await self.retrieval_service.retrieve_context(
            query=query,
            top_k=settings.TOP_K,
            collection_name=collection_name,
        )

        combined = f"PROJECT FILES:\n{project_context}\n\nGENERAL LAWS:\n{main_context}"
        return combined, project_context, main_context

    async def _retrieve_standard_context(
        self, query: str, collection_name: str, file_context: str | None
    ) -> str:
        """Retrieve standard context from vector DB."""
        context = await self.retrieval_service.retrieve_context(
            query=query,
            top_k=settings.TOP_K,
            collection_name=collection_name,
        )

        if file_context:
            context = f"{context}\nFile Content:\n{file_context}"

        return context

    def _format_chat_history(self, chat_history: list | None) -> str:
        """Format recent chat history for inclusion in prompt."""
        if not chat_history:
            return ""

        recent = chat_history[-settings.CHAT_HISTORY_LIMIT:]
        lines = ["Previous Conversation History:"]

        for idx, entry in enumerate(recent, start=1):
            lines.append(f"{idx}. User: {entry.question}")
            lines.append(f"   Assistant: {entry.answer}")

        lines.append("Use the above conversation to maintain context.")
        return "\n".join(lines)

    def _build_system_prompt(
        self,
        project_id: str | None,
        collection_name: str,
        context: str,
        chat_history: str,
        project_context: str | None = None,
        main_context: str | None = None,
    ) -> str:
        """Build system prompt from template and context."""
        # Get appropriate template
        if project_id:
            template = AssistantConfig.get_assistant_prompt_template("project_file")
            return template.format(
                project_context=project_context,
                main_context=main_context,
                chat_history=chat_history,
            )

        assistant_type = (
            "soliq"
            if collection_name == settings.MILVUS_SOLIQ_ASSISTANT_NAME
            else "main"
        )
        template = AssistantConfig.get_assistant_prompt_template(assistant_type)
        return template.format(context=context, chat_history=chat_history)

    # Response Generation
    async def _generate_stream(
        self, llm: LLM, query: str, gen_context: GenerationContext
    ) -> AsyncGenerator[str, None]:
        """Generate streaming response with fallback."""
        if settings.DEVELOPMENT_MODE:
            yield gen_context.debug_data

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

    async def _generate_non_stream(
        self, llm: LLM, query: str, gen_context: GenerationContext
    ) -> str | tuple[str, dict[str, Any]]:
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

        if settings.DEVELOPMENT_MODE:
            return response, gen_context.debug_data
        return response

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

    def _create_error_response(
        self, stream: bool, debug_data: dict[str, Any]
    ) -> str | AsyncGenerator[str, None]:
        """Create error response in appropriate format."""
        error_msg = "Sorry, I couldn't generate an answer at the moment."

        if stream:

            async def error_gen():
                yield error_msg

            return error_gen()

        return error_msg
