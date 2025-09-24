from typing import Union, AsyncGenerator, Optional, List
from app.retrieval.retrieval_service import RetrievalService
from app.core.config import settings
from app.llms.base import LLM
from app.services.language_service import LanguageDetector
from app.core.logger import logger

# Import all supported LLMs
from app.llms.gpt import ChatGPT
from app.llms.novita import Novita
from app.llms.local_vllm import LocalVLLM

class ChatChain:
    """
    Main chain to handle retrieval-augmented generation (RAG):
    1. Retrieve top documents from vector DB.
    2. Generate final answer using selected LLM.
    """

    def __init__(self):
        self.retrieval_service = RetrievalService()
        self.llm = self._get_llm_provider()
        self.llm_fallback = ChatGPT() # Fallback to OpenAI GPT if needed
        self.language_detector = LanguageDetector()

    def _get_llm_provider(self) -> LLM:
        """Factory method to select and load the Gemma model based on settings."""
        providers = {
            "novita": Novita,
            "local": LocalVLLM,
        }
        return providers.get(settings.LLM_PROVIDER, Novita)()

    async def _format_chat_history(self, chat_history: Optional[List]) -> str:
        """Format chat history for use in prompt context."""
        if not chat_history or len(chat_history) == 0:
            return ""

        recent_history = chat_history[-settings.CHAT_HISTORY_LIMIT:]

        formatted = "\n\nPrevious Conversation History:\n"
        for idx, pair in enumerate(recent_history, start=1):
            formatted += f"{idx}. User: {pair.question}\n   Assistant: {pair.answer}\n"

        return formatted + "Use the above conversation to maintain context."

    async def generate_answer(
        self,
        query: str,
        chat_history: Optional[List] = None,
    ) -> Union[str, AsyncGenerator[str, None]]:
        """
        Generate a response to the user's query using RAG approach.
        Falls back to ChatGPT if the primary LLM fails.
        """
        try:
            language = self.language_detector.detect_language(query)
            instruction = self.language_detector.get_instruction(language)
            context = await self.retrieval_service.retrieve_context(query=query)
            chat_history_text = await self._format_chat_history(chat_history)

            logger.debug(f"[ChatChain] Retrieved context: {context}")

            # Try primary LLM first
            try:
                response = await self.llm.generate_response(
                    query=query,
                    context=context,
                    chat_history_text=chat_history_text,
                    language_instruction=instruction
                )
            except Exception as llm_error:
                # Fallback to ChatGPT on failure
                logger.warning(f"[ChatChain] Primary LLM failed, falling back to ChatGPT: {llm_error}", exc_info=True)
                response = await self.llm_fallback.generate_response(
                    query=query,
                    context=context,
                    chat_history_text=chat_history_text,
                    language_instruction=instruction
                )

            if settings.STREAM:
                return self._stream_response(response, language)
            else:
                logger.info(f"[DEBUG] LLM full response: {response}")
                return response

        except Exception as e:
            logger.error(f"[ChatChain] Generation failed: {e}", exc_info=True)
            error_msg = "Sorry, I couldn't generate an answer at the moment."
            return await self._stream_or_return_error(error_msg)

    async def _stream_or_return_error(self, message: str) -> Union[str, AsyncGenerator]:
        """Return either string or stream depending on STREAM setting."""
        if settings.STREAM:
            return self._error_stream(message)
        else:
            return message

    async def _stream_response(self, response_generator: AsyncGenerator[str, None], language: str) -> AsyncGenerator[str, None]:
        """Handle streaming response and save conversation when complete."""
        full_response = ""
        
        async for chunk in response_generator:
            # Yield characters one by one and accumulate the full response
            for char in chunk:
                yield char
                full_response += char

        logger.debug(f"[ChatChain] Full LLM Response: {full_response}")

    async def _error_stream(self, message: str) -> AsyncGenerator[str, None]:
        """Yield error message as stream."""
        for char in message:
            yield char