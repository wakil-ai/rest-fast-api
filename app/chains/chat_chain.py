from typing import Union, AsyncGenerator, Optional, List, Any
from app.retrieval.retrieval_service import RetrievalService
from app.core.config import settings
from app.llms.base import LLM
from app.core.logger import logger

# Import all supported LLMs
from app.llms.gpt import ChatGPT
from app.llms.novita import Novita
from app.llms.local_vllm import LocalVLLM
from app.llms.claude import Claude
from app.services.memory_service import ChatMemoryService
from app.chains.prompts import PROMPT, SOLIQ_PROMPT

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
        self.llm_fallback = ChatGPT() 

    def replace_punctuation(self, text: str) -> str:
        """Replace special punctuation characters with standard ones."""
        replacements = {
            '【': '[',
            '】': ']',
        }
        for old, new in replacements.items():
            text = text.replace(old, new)
        return text

    def _get_llm_provider(self) -> LLM:
        """Factory method to select and load the Gemma model based on settings."""
        providers = {
            "novita": Novita,
            "local": LocalVLLM,
        }
        return providers.get(settings.LLM_PROVIDER, Novita)()
    
    def _get_llm_by_model(self, model_name: str) -> LLM:
        """
        Factory method to get LLM instance based on model name.
        
        Args:
            model_name: The model identifier (e.g., 'gpt-4o', 'claude-3-5-sonnet-20241022', 'gpt-oss-120b')
        
        Returns:
            LLM instance configured for the specified model
        """
        # OpenAI models
        logger.debug(f"[ChatChain] Getting LLM by model: {model_name}")
        
        # Novita models (check before ChatGPT to avoid gpt-oss-* being matched as gpt-*)
        if model_name.startswith("gemma-") or model_name.startswith("gpt-oss-"):
            return Novita(model_name=model_name)
        
        # ChatGPT models
        elif model_name.startswith("gpt-"):
            return ChatGPT(model_name=model_name)
        
        # Claude/Anthropic models
        elif model_name.startswith("claude-"):
            return Claude(model_name=model_name)
        
        # Default fallback to current configured provider
        else:
            logger.warning(f"[ChatChain] Unknown model '{model_name}', using default provider")
            return self._get_llm_provider()

    async def _format_chat_history(self, chat_history: Optional[List]) -> str:
        """Format chat history for use in prompt context."""
        if not chat_history or len(chat_history) == 0:
            return ""

        recent_history = chat_history[-settings.CHAT_HISTORY_LIMIT:]

        formatted = "\n\nPrevious Conversation History:\n"
        for idx, pair in enumerate(recent_history, start=1):
            formatted += f"{idx}. User: {pair.question}\n   Assistant: {pair.answer}\n"

        return formatted + "Use the above conversation to maintain context."
    
    async def make_system_prompt(self, context: str, 
                                   chat_history_text: str, 
                                   prompt_template: Any = PROMPT) -> str:
        """Create the system prompt using the provided context and chat history."""
        return prompt_template.format(context=context, chat_history=chat_history_text)

    async def generate_answer(
        self,
        user_id: str,
        query: str,
        chat_history: Optional[List] = None,
        stream: bool = settings.STREAM,
        file_context: Optional[str] = None,
        collection_name: str = settings.MILVUS_MAIN_NAME,
        model_name: Optional[str] = None,
    ) -> Union[str, AsyncGenerator[str, None]]:
        """
        Generate a response to the user's query using RAG approach.
        Falls back to ChatGPT if the primary LLM fails.
        """
        try:
            # Select LLM based on model_name if provided, otherwise use default
            selected_llm = self._get_llm_by_model(model_name) if model_name else self.llm
            
            # Select the appropriate prompt template based on collection
            prompt_template = SOLIQ_PROMPT if collection_name == settings.MILVUS_SOLIQ_ASSISTANT_NAME else PROMPT
            # If file context is provided, prepend it to the retrieved context so LLM uses file content
            # Retrieve relavant documents query + file context if file provided
            context = ""
            
            if file_context:
                # Merge file context and query context 
                merged_query = f"{query} \n\n File Content: {file_context}"
                context_with_query = await self.retrieval_service.retrieve_context(query=merged_query, top_k=settings.TOP_K, collection_name=collection_name)
                context = f"""{context_with_query}
                
                File Content:
                Use the following extracted text from the uploaded file to answer the question:
                {file_context}"""
                
            else:  
                context = await self.retrieval_service.retrieve_context(query=query, top_k=settings.TOP_K, collection_name=collection_name)

            memory_text = await self.mem_service.search_memory(user_id, query)
            
            chat_history_text = await self._format_chat_history(chat_history)
            
            # Merge Chat History and Memory
            if chat_history_text and memory_text:
                chat_history_text += memory_text
            elif memory_text:
                chat_history_text += memory_text
                
            system_prompt = await self.make_system_prompt(
                context=context,
                chat_history_text=chat_history_text,
                prompt_template=prompt_template
            )
                
            logger.debug(f"[ChatChain] System Prompt: {system_prompt}")

            if stream:
                async def stream_generator() -> AsyncGenerator[str, None]:
                    try:
                        # Attempt selected LLM
                        response_generator = await selected_llm.generate_response(
                            user_prompt=query,
                            system_prompt=system_prompt,
                            stream=stream
                        )
                        async for chunk in self._stream_response(response_generator):
                            yield chunk
                    except Exception as llm_error:
                        logger.warning(f"[ChatChain] Primary LLM streaming failed, falling back to ChatGPT.", exc_info=True)
                        try:
                            # Attempt fallback LLM
                            fallback_generator = await self.llm_fallback.generate_response(
                                user_prompt=query,
                                system_prompt=system_prompt,
                                stream=stream
                            )
                            async for chunk in self._stream_response(fallback_generator):
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
                    response = await selected_llm.generate_response(
                        user_prompt=query,
                        system_prompt=system_prompt,
                        stream=stream
                    )
                except Exception as llm_error:
                    logger.warning(f"[ChatChain] Primary LLM failed, falling back to ChatGPT.", exc_info=True)
                    try:
                        response = await self.llm_fallback.generate_response(
                            user_prompt=query,
                            system_prompt=system_prompt,
                            stream=stream
                        )
                    except Exception as fallback_error:
                        logger.error(f"[ChatChain] Fallback LLM also failed.", exc_info=True)
                        raise fallback_error # Re-raise to be caught by the outer handler

                response = self.replace_punctuation(response)
                
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

    async def _stream_response(self, response_generator: AsyncGenerator[str, None]) -> AsyncGenerator[str, None]:
        """Handle streaming response and save conversation when complete."""
        full_response, buffer = "", ""
        
        async for chunk in response_generator:
            buffer += chunk
            if buffer.endswith('\n'):
                buffer = self.replace_punctuation(buffer)
                
                for char in buffer:
                    yield char
                full_response += buffer
                buffer = ""
                
        if buffer:
            for char in buffer:
                yield char
            full_response += buffer

        logger.debug(f"[ChatChain] Full LLM Response: {full_response}")
        
    async def _error_stream(self, message: str) -> AsyncGenerator[str, None]:
        """Yield error message as stream."""
        for char in message:
            yield char