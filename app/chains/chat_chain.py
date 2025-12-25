from typing import Union, AsyncGenerator, Optional, List, Any, Dict, Tuple

from app.retrieval.retrieval_service import RetrievalService
from app.core.config import settings
from app.llms.base import LLM
from app.core.logger import logger
from app.llms.gpt import ChatGPT
from app.llms.novita import Novita
from app.llms.local_vllm import LocalVLLM
from app.llms.claude import Claude
from app.services.memory_service import ChatMemoryService
from app.chains.prompts import PROMPT, SOLIQ_PROMPT


class ChatChain:
    """
    Main chain for retrieval-augmented generation (RAG):
    1. Retrieve relevant documents from vector DB.
    2. Generate answer using selected LLM.
    """

    def __init__(self):
        self.retrieval_service = RetrievalService()
        self.llm = self._get_default_llm()
        self.memory_service = ChatMemoryService()
        self.llm_fallback = ChatGPT()

    @staticmethod
    def replace_punctuation(text: str) -> str:
        """Replace special punctuation with standard equivalents."""
        replacements = {"【": "[", "】": "]"}
        for old, new in replacements.items():
            text = text.replace(old, new)
        return text

    def _get_default_llm(self) -> LLM:
        """Return the default LLM based on settings.LLM_PROVIDER."""
        providers = {"novita": Novita, "local": LocalVLLM}
        provider_class = providers.get(settings.LLM_PROVIDER, Novita)
        return provider_class()

    def _get_llm_by_model(self, model_name: str) -> LLM:
        """Factory to instantiate LLM based on model name."""
        logger.debug(f"[ChatChain] Selecting LLM for model: {model_name}")

        if model_name.startswith("gemma-") or model_name.startswith("gpt-oss-"):
            return Novita(model_name=model_name)
        if model_name.startswith("gpt-"):
            return ChatGPT(model_name=model_name)
        if model_name.startswith("claude-"):
            return Claude(model_name=model_name)

        logger.warning(f"[ChatChain] Unknown model '{model_name}', using default provider")
        return self._get_default_llm()

    async def _format_chat_history(self, chat_history: Optional[List]) -> str:
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

    async def make_system_prompt(
        self,
        context: str,
        chat_history_text: str,
        prompt_template: Any = PROMPT,
    ) -> str:
        """Build system prompt with context and history."""
        return prompt_template.format(
            context=context, chat_history=chat_history_text
        )

    async def generate_answer(
        self,
        user_id: str,
        query: str,
        chat_history: Optional[List] = None,
        stream: bool = settings.STREAM,
        file_context: Optional[str] = None,
        collection_name: str = settings.MILVUS_MAIN_NAME,
        model_name: Optional[str] = None,
    ) -> Union[str, AsyncGenerator[str, None], Tuple[str, Dict[str, Any]]]:
        """
        Generate response using RAG.
        Falls back to ChatGPT if primary LLM fails.
        """
        debug_data: Dict[str, Any] = {"retrieved_contents": []}

        try:
            # Select LLM
            selected_llm = (
                self._get_llm_by_model(model_name) if model_name else self.llm
            )
            prompt_template = (
                SOLIQ_PROMPT
                if collection_name == settings.MILVUS_SOLIQ_ASSISTANT_NAME
                else PROMPT
            )

            # Build query with optional file content
            merged_query = (
                f"{query}\n\nFile Content: {file_context}"
                if file_context
                else query
            )

            # Retrieve context
            context_result = await self.retrieval_service.retrieve_context(
                query=merged_query,
                top_k=settings.TOP_K,
                collection_name=collection_name
            )

            context = (
                f"{context_result}\nFile Content:\nUse the following extracted text from the uploaded file to answer the question:\n{file_context}"
                if file_context
                else context_result
            )

            if settings.DEVELOPMENT_MODE:
                debug_data["retrieved_contents"] = context

            # Memory and history
            memory_text = await self.memory_service.search_memory(user_id, query)
            chat_history_text = await self._format_chat_history(chat_history)

            if chat_history_text and memory_text:
                chat_history_text += "\n" + memory_text
            elif memory_text:
                chat_history_text = memory_text

            system_prompt = await self.make_system_prompt(
                context=context,
                chat_history_text=chat_history_text,
                prompt_template=prompt_template,
            )

            logger.debug(f"[ChatChain] System Prompt: {system_prompt}")

            if stream:
                return self._stream_response(
                    selected_llm, query, system_prompt, debug_data
                )
            else:
                return await self._non_stream_response(
                    selected_llm, query, system_prompt, debug_data
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
        debug_data: Dict[str, Any],
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
        except Exception as primary_error:
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
            except Exception as fallback_error:
                logger.error("[ChatChain] Fallback LLM failed.", exc_info=True)
                error_msg = "Sorry, I couldn't generate an answer at the moment."
                async for char in error_msg:
                    yield char

    async def _non_stream_response(
        self,
        selected_llm: LLM,
        user_prompt: str,
        system_prompt: str,
        debug_data: Dict[str, Any],
    ) -> Union[str, Tuple[str, Dict[str, Any]]]:
        """Non-streaming response with fallback handling."""
        try:
            response = await selected_llm.generate_response(
                user_prompt=user_prompt,
                system_prompt=system_prompt,
                stream=False,
            )
        except Exception as primary_error:
            logger.warning(
                "[ChatChain] Primary LLM failed, using fallback.", exc_info=True
            )
            try:
                response = await self.llm_fallback.generate_response(
                    user_prompt=user_prompt,
                    system_prompt=system_prompt,
                    stream=False,
                )
            except Exception as fallback_error:
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
        """Yield individual characters after cleaning punctuation."""
        buffer = ""
        async for chunk in response_generator:
            buffer += chunk
            if buffer.endswith("\n"):
                cleaned = self.replace_punctuation(buffer)
                for char in cleaned:
                    yield char
                buffer = ""
        if buffer:
            cleaned = self.replace_punctuation(buffer)
            for char in cleaned:
                yield char

    async def _handle_error(
        self, message: str, debug_data: Dict[str, Any], stream: bool
    ) -> Union[str, Tuple[str, Dict[str, Any]], AsyncGenerator[str, None]]:
        """Return error message in appropriate format."""
        if settings.DEVELOPMENT_MODE and not stream:
            return message, debug_data
        if stream:
            return self._error_generator(message)
        return message

    async def _error_generator(self, message: str) -> AsyncGenerator[str, None]:
        """Yield error message character by character."""
        for char in message:
            yield char