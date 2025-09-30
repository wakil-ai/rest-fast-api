from typing import Union, AsyncGenerator, Optional, List, Any
from app.retrieval.retrieval_service import RetrievalService
from app.core.config import settings
from app.llms.base import LLM
from app.services.language_service import LanguageDetector
from app.core.logger import logger

# Import all supported LLMs
from app.llms.gpt import ChatGPT
from app.llms.novita import Novita
from app.llms.local_vllm import LocalVLLM
from app.services.memory_service import ChatMemoryService

class ChatChain:
    """
    Main chain to handle retrieval-augmented generation (RAG):
    1. Retrieve top documents from vector DB.
    2. Generate final answer using selected LLM.
    """

    def __init__(self):
        self.retrieval_service = RetrievalService()
        self.llm = self._get_llm_provider()
        self.mem_service = ChatMemoryService()
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
        user_id: str,
        query: str,
        chat_history: Optional[List] = None,
        stream: bool = settings.STREAM
    ) -> Union[str, AsyncGenerator[str, None]]:
        """
        Generate a response to the user's query using RAG approach.
        Falls back to ChatGPT if the primary LLM fails.
        """
        try:
            language = self.language_detector.detect_language(query)
            instruction = self.language_detector.get_instruction(language)
            context = await self.retrieval_service.retrieve_context(query=query)
            
            memory_text = await self.mem_service.search_memory(user_id, query)
            
            chat_history_text = await self._format_chat_history(chat_history)
            
            # Merge Chat History and Memory
            if chat_history_text and memory_text:
                chat_history_text += memory_text
            elif memory_text:
                chat_history_text += memory_text
                
            logger.debug(f"[ChatChain] Chat History text: {chat_history_text}")
            logger.debug(f"[ChatChain] Retrieved context: {context}")

            if stream:
                async def stream_generator() -> AsyncGenerator[str, None]:
                    try:
                        # Attempt primary LLM
                        response_generator = await self.llm.generate_response(
                            query=query,
                            context=context,
                            chat_history_text=chat_history_text,
                            language_instruction=instruction,
                            stream=stream
                        )
                        async for chunk in self._stream_response(response_generator, language, query, user_id):
                            yield chunk
                    except Exception as llm_error:
                        logger.warning(f"[ChatChain] Primary LLM streaming failed, falling back to ChatGPT.", exc_info=True)
                        try:
                            # Attempt fallback LLM
                            fallback_generator = await self.llm_fallback.generate_response(
                                query=query,
                                context=context,
                                chat_history_text=chat_history_text,
                                language_instruction=instruction,
                                stream=stream
                            )
                            async for chunk in self._stream_response(fallback_generator, language, query, user_id):
                                yield chunk
                        except Exception as fallback_error:
                            logger.error(f"[ChatChain] Fallback LLM also failed.", exc_info=True)
                            error_msg = "Sorry, I couldn't generate an answer at the moment."
                            async for chunk in self._error_stream(error_msg):
                                yield chunk
                return stream_generator()
            else:
                # Non-streaming fallback logic
                try:
                    response = await self.llm.generate_response(
                        query=query,
                        context=context,
                        chat_history_text=chat_history_text,
                        language_instruction=instruction, 
                        stream=stream
                    )
                except Exception as llm_error:
                    logger.warning(f"[ChatChain] Primary LLM failed, falling back to ChatGPT.", exc_info=True)
                    try:
                        response = await self.llm_fallback.generate_response(
                            query=query,
                            context=context,
                            chat_history_text=chat_history_text,
                            language_instruction=instruction,
                            stream=stream
                        )
                    except Exception as fallback_error:
                        logger.error(f"[ChatChain] Fallback LLM also failed.", exc_info=True)
                        raise fallback_error # Re-raise to be caught by the outer handler

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

    async def _stream_response(self, response_generator: AsyncGenerator[str, None], language: str, query: str, user_id: str) -> AsyncGenerator[str, None]:
        """Handle streaming response and save conversation when complete."""
        full_response, buffer = "", ""
        
        logger.debug(f"[ChatChain] {language}")
        
        async for chunk in response_generator:
            buffer += chunk
            if buffer.endswith('\n'):
                buffer = self.language_detector.correct_language(buffer, language)
                for char in buffer:
                    yield char
                full_response += buffer
                buffer = ""
                
        if buffer:
            buffer = self.language_detector.correct_language(buffer, language)
            for char in buffer:
                yield char
            full_response += buffer

        logger.debug(f"[ChatChain] Full LLM Response: {full_response}")
        
    async def _error_stream(self, message: str) -> AsyncGenerator[str, None]:
        """Yield error message as stream."""
        for char in message:
            yield char