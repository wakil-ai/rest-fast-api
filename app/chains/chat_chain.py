from collections.abc import AsyncGenerator
from typing import Any

from app.chains.prompts import PROJECT_FILE_PROMPT, PROMPT, SOLIQ_PROMPT
from app.core.config import settings
from app.core.logger import logger
from app.llms.base import LLM
from app.llms.claude import Claude
from app.llms.gpt import ChatGPT
from app.llms.novita import Novita
from app.retrieval.retrieval_service import RetrievalService
from app.services.memory_service import ChatMemoryService


class ChatChain:
    """
    Main chain for retrieval-augmented generation (RAG):
    1. Retrieve relevant documents from vector DB.
    2. Generate answer using selected LLM.
    """

    def __init__(self):
        self.retrieval_service = RetrievalService()
        self.memory_service = ChatMemoryService()
        self.llm_fallback = ChatGPT()

    @staticmethod
    def replace_punctuation(text: str) -> str:
        """Replace special punctuation with standard equivalents."""
        replacements = {"【": "[", "】": "]"}
        for old, new in replacements.items():
            text = text.replace(old, new)
        return text

    def _get_llm_by_model(self, model_name: str) -> LLM:
        """Factory to instantiate LLM based on model name."""
        logger.debug(f"[ChatChain] Selecting LLM for model: {model_name}")

        if model_name.startswith("gemma-") or model_name.startswith("gpt-oss-"):
            return Novita(model_name=model_name)
        if model_name.startswith("gpt-"):
            return ChatGPT(model_name=model_name)
        if model_name.startswith("claude-"):
            return Claude(model_name=model_name)

        logger.warning(
            f"[ChatChain] Unknown model '{model_name}', using default provider"
        )
        return self.llm_fallback

    async def _format_chat_history(self, chat_history: list | None) -> str:
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

    async def make_system_prompt(
        self,
        context: str,
        chat_history_text: str,
        prompt_template: Any = PROMPT,
    ) -> str:
        """Build system prompt with context and history."""
        return prompt_template.format(context=context, chat_history=chat_history_text)

    async def run(
        self,
        query: str,
        system_prompt: str,
        stream: bool = settings.STREAM,
        llm: LLM | None = None,
        debug_data: dict[str, Any] = None,
    ):
        if llm is None:
            llm = self.llm_fallback
        if stream:
            return self._stream_response(llm, query, system_prompt, debug_data)
        else:
            return await self._non_stream_response(
                llm, query, system_prompt, debug_data
            )

    async def generate_answer(
        self,
        user_id: str,
        query: str,
        chat_history: list | None = None,
        stream: bool = settings.STREAM,
        file_context: str | None = None,
        project_id: str | None = None,
        collection_name: str = settings.MILVUS_MAIN_NAME,
        model_name: str | None = None,
    ) -> str | AsyncGenerator[str, None] | tuple[str, dict[str, Any]]:
        """
        Generate response using RAG.
        Falls back to ChatGPT if primary LLM fails.
        """
        debug_data: dict[str, Any] = {"retrieved_contents": []}

        try:
            # Select LLM
            selected_llm = (
                self._get_llm_by_model(model_name) if model_name else self.llm_fallback
            )

            if project_id:
                prompt_template = PROJECT_FILE_PROMPT
            else:
                prompt_template = (
                    SOLIQ_PROMPT
                    if collection_name == settings.MILVUS_SOLIQ_ASSISTANT_NAME
                    else PROMPT
                )

            # Build query with optional file content (legacy support)
            merged_query = (
                f"{query}\n\nFile Content: {file_context}" if file_context else query
            )

            # Retrieve context
            if project_id:
                # Dual context retrieval
                project_context = await self.retrieval_service.retrieve_project_context(
                    query=query,
                    project_id=project_id,
                    user_id=user_id,
                    top_k=settings.TOP_K // 2,
                )
                main_context = await self.retrieval_service.retrieve_context(
                    query=query,
                    top_k=settings.TOP_K // 2,
                    collection_name=collection_name,
                )
                context = f"PROJECT FILES:\n{project_context}\n\nGENERAL LAWS:\n{main_context}"
            else:
                context = await self.retrieval_service.retrieve_context(
                    query=merged_query,
                    top_k=settings.TOP_K,
                    collection_name=collection_name,
                )
                if file_context:
                    context = f"{context}\nFile Content:\n{file_context}"

            if settings.DEVELOPMENT_MODE:
                debug_data["retrieved_contents"] = context

            # Memory and history
            memory_text = await self.memory_service.search_memory(user_id, query)
            chat_history_text = await self._format_chat_history(chat_history)

            if chat_history_text and memory_text:
                chat_history_text += "\n" + memory_text
            elif memory_text:
                chat_history_text = memory_text

            # Prepare System Prompt
            if project_id:
                # Use PROJECT_FILE_PROMPT format
                system_prompt = prompt_template.format(
                    project_context=project_context,
                    main_context=main_context,
                    chat_history=chat_history_text,
                )
            else:
                system_prompt = await self.make_system_prompt(
                    context=context,
                    chat_history_text=chat_history_text,
                    prompt_template=prompt_template,
                )

            logger.debug(f"[ChatChain] System Prompt: {system_prompt}")

            return await self.run(
                query=query,
                system_prompt=system_prompt,
                stream=stream,
                llm=selected_llm,
                debug_data=debug_data,
            )

        except Exception as e:
            logger.error(f"[ChatChain] Generation failed: {e}", exc_info=True)
            error_msg = "Sorry, I couldn't generate an answer at the moment."
            if stream:
                return self._error_generator(error_msg)
            elif settings.DEVELOPMENT_MODE:
                return error_msg, debug_data
            else:
                return error_msg

    async def _stream_response(
        self,
        selected_llm: LLM,
        user_prompt: str,
        system_prompt: str,
        debug_data: dict[str, Any],
    ) -> AsyncGenerator[str, None]:
        """Stream response with fallback handling."""
        if settings.DEVELOPMENT_MODE:
            yield debug_data

        try:
            response_gen = await selected_llm.generate_response(
                user_prompt=user_prompt,
                system_prompt=system_prompt,
                stream=True,
            )
            async for chunk in self._yield_clean_chunks(response_gen):
                yield chunk
        except Exception:
            logger.warning(
                "[ChatChain] Primary LLM streaming failed, using fallback.",
                exc_info=True,
            )
            try:
                fallback_gen = await self.llm_fallback.generate_response(
                    user_prompt=user_prompt,
                    system_prompt=system_prompt,
                    stream=True,
                )
                async for chunk in self._yield_clean_chunks(fallback_gen):
                    yield chunk
            except Exception:
                logger.error("[ChatChain] Fallback LLM failed.", exc_info=True)
                error_msg = "Sorry, I couldn't generate an answer at the moment."
                yield error_msg

    async def _non_stream_response(
        self,
        selected_llm: LLM,
        user_prompt: str,
        system_prompt: str,
        debug_data: dict[str, Any],
    ) -> str | tuple[str, dict[str, Any]]:
        """Non-streaming response with fallback handling."""
        try:
            response = await selected_llm.generate_response(
                user_prompt=user_prompt,
                system_prompt=system_prompt,
                stream=False,
            )
        except Exception:
            logger.warning(
                "[ChatChain] Primary LLM failed, using fallback.", exc_info=True
            )
            try:
                response = await self.llm_fallback.generate_response(
                    user_prompt=user_prompt,
                    system_prompt=system_prompt,
                    stream=False,
                )
            except Exception:
                logger.error("[ChatChain] Fallback failed.", exc_info=True)
                raise

        response = self.replace_punctuation(response)
        logger.info(f"[DEBUG] LLM full response: {response}")

        if settings.DEVELOPMENT_MODE:
            return response, debug_data
        return response

    async def _yield_clean_chunks(
        self, response_generator: AsyncGenerator[str, None]
    ) -> AsyncGenerator[str, None]:
        """Yield cleaned chunks immediately."""
        async for chunk in response_generator:
            if chunk:
                # Clean punctuation on the chunk level
                cleaned = self.replace_punctuation(chunk)
                yield cleaned

    async def _handle_error(
        self, message: str, debug_data: dict[str, Any], stream: bool
    ) -> str | tuple[str, dict[str, Any]] | AsyncGenerator[str, None]:
        """Return error message in appropriate format."""
        if settings.DEVELOPMENT_MODE and not stream:
            return message, debug_data
        if stream:
            return self._error_generator(message)
        return message

    async def _error_generator(self, message: str) -> AsyncGenerator[str, None]:
        """Yield error message as a single chunk."""
        yield message
