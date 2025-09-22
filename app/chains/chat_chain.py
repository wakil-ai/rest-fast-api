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
        self.gpt = ChatGPT()
        self.gemma = self._get_gemma_model()
        self.language_detector = LanguageDetector()

    def _get_gemma_model(self) -> LLM:
        """Factory method to select and load the Gemma model based on settings."""
        providers = {
            "novita": Novita,
            "local": LocalVLLM,
        }

        provider = settings.GEMMA_PROVIDER

        logger.info(f"[ChatChain] Selected Gemma provider: {provider}")

        if provider not in providers:
            raise ValueError(f"[ChatChain] Unsupported Gemma provider: {provider}")

        return providers[provider]()

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
        llm_type: str,
        query: str,
        top_k: int = settings.TOP_K,
        chat_history: Optional[List] = None,
    ) -> Union[str, AsyncGenerator[str, None]]:
        """
        Generate a response to the user's query using RAG approach.

        Returns:
            - str: Final answer if streaming is disabled
            - AsyncGenerator: Streaming response if enabled
        """
        try:
            language = self.language_detector.detect_language(query)
            instruction = self.language_detector.get_instruction(language)

            context = await self.retrieval_service.retrieve_context(
                query=query, top_k=top_k
            )
            chat_history_text = await self._format_chat_history(chat_history)

            logger.info(f"[ChatChain] Retrieved context: {context}")

            llm_map = {"gpt": self.gpt, "gemma": self.gemma} 
            llm = llm_map.get(llm_type)

            if llm is None:
                error_msg = "Invalid LLM selected. Please choose 'gpt' or 'gemma'."
                logger.warning(f"[ChatChain] {error_msg}")
                return await self._stream_or_return_error(error_msg)

            logger.info(f"[ChatChain] Sending query to {llm_type}: {query}")
            response = await llm.generate_response(
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
        full_response, buffer = "", ""
        async for chunk in response_generator:
            buffer += chunk
            if buffer.endswith(('\n')): # Line by line convert
                buffer = self.language_detector.correct_language(buffer, language)
                for char in buffer:
                    yield char
                full_response += buffer
                buffer = ""
    
        # Last buffer
        if buffer:
            buffer = self.language_detector.correct_language(buffer, language)
            # Yield the buffer word by word
            for char in buffer:
                yield char
            full_response += buffer

        logger.info(f"[ChatChain] Full LLM Response: {full_response}")

    async def _error_stream(self, message: str) -> AsyncGenerator[str, None]:
        """Yield error message as stream."""
        for char in message:
            yield char