# app/orchestration/flow.py

import json
from typing import Any, Dict

from crewai.flow.flow import Flow, listen, start, router
from app.utils.streaming import format_progress_event
from crewai.types.streaming import StreamChunkType

from app.orchestration.crews import Crews
from app.orchestration.schemas import (
    AgenticRAGState,
    MemoryAgentResponse,
    RetrievalStrategyResponse,
    ContextEvaluationResponse,
    WebSearchResponse,
    ProgressEventType
)
from app.retrieval.retrieval_service import RetrievalService
from app.services.memory_service import ChatMemoryService
from app.core.config import settings
from app.core.logger import logger

class AgenticRAGFlow(Flow[AgenticRAGState]):
    """
    Agentic RAG pipeline with memory, retrieval strategy, context evaluation, and web search.
    
    Flow:
        1. Memory Retrieval (session + personal context)
        2. Retrieval Strategy & Assistant Selection
        3. Document Retrieval
        4. Context Evaluation
        5. Web Search (conditional - only if context insufficient)
        6. Answer Generation
    """
    def __init__(self, enable_progress_stream: bool = False, progress_callback=None):
        super().__init__(tracing=settings.TRACING)
        self.enable_progress_stream = enable_progress_stream
        self.progress_callback = progress_callback
        self._initialize_services()
        self._initialize_crews()
    
    def _initialize_services(self) -> None:
        """Initialize all required services."""
        self.retrieval_service = RetrievalService()
        self.memory_service = ChatMemoryService()
    
    def _initialize_crews(self) -> None:
        """Initialize and cache all crews once."""
        crews_factory = Crews()
        self.memory_crew = crews_factory.memory_crew()
        self.retrieval_strategy_crew = crews_factory.retrieval_strategy_crew()
        self.evaluation_crew = crews_factory.evaluation_crew()
        self.web_search_crew = crews_factory.web_search_crew()
        self.answer_crews = {
            "umumiy": crews_factory.answer_crew("umumiy"),
            "soliq": crews_factory.answer_crew("soliq"),
        }
    
    async def _emit_progress(self, event_type: str, status: str, message: str, details: dict = None) -> None:
        """Emit progress event if callback is registered."""
        if self.progress_callback:
            event = await format_progress_event(event_type, status, message, details)
            await self.progress_callback(event)
    
    @start()
    async def start_memory_retrieval(self) -> None:
        """Retrieve and summarize session and personal memory."""        
        await self._emit_progress(
            ProgressEventType.MEMORY_RETRIEVAL,
            "in_progress",
            "Analyzing your conversation history and preferences..."
        )
        
        try:
            session_memory, personal_memory = await self._fetch_memories()
            
            if not await self._has_any_memory(session_memory, personal_memory):
                logger.info("No memories found; skipping summarization.")
                await self._set_default_memory()
                await self._emit_progress(
                    ProgressEventType.MEMORY_RETRIEVAL,
                    "completed",
                    "Memory context loaded"
                )
                return
            
            memory_response = await self._summarize_memory(session_memory, personal_memory)
            await self._apply_memory_response(memory_response)
            
            self.state.enriched_query = await self._enrich_query_with_memory(self.state.query)
            
            # Notify if query was enriched
            if self.state.enriched_query and self.state.enriched_query != self.state.query:
                await self._emit_progress(
                    ProgressEventType.MEMORY_RETRIEVAL,
                    "in_progress",
                    "Query enriched with conversation context for better retrieval",
                )
            
            await self._emit_progress(
                ProgressEventType.MEMORY_RETRIEVAL,
                "completed",
                "Memory context loaded successfully"
            )
            
        except Exception as error:
            await self._handle_error("Memory retrieval", error)
            await self._set_default_memory()
            await self._emit_progress(
                ProgressEventType.MEMORY_RETRIEVAL,
                "completed",
                "Proceeding without memory context"
            )
    
    @listen(start_memory_retrieval)
    async def determine_retrieval_strategy(self) -> None:
        """Analyze query to select optimal retrieval strategy."""        
        await self._emit_progress(
            ProgressEventType.RETRIEVAL_STRATEGY,
            "in_progress",
            "Determining best search strategy for your question..."
        )
        
        try:
            # Build query input for retrieval crew
            query_input = {"query": self.state.enriched_query or self.state.query}
            result = await self.retrieval_strategy_crew.kickoff_async(
                inputs=query_input,
            )
            strategy_response = await self._parse_structured_output(result, RetrievalStrategyResponse)
            
            self.state.retrieval_output = strategy_response.model_dump()
            self.state.selected_assistant = strategy_response.assistant
            
            # Check if query was rewritten
            query_rewritten = strategy_response.query_rewrite != self.state.query
            
            # Notify about query rewrite if it happened
            if query_rewritten:
                await self._emit_progress(
                    ProgressEventType.RETRIEVAL_STRATEGY,
                    "in_progress",
                    strategy_response.reasoning,
                )
            
            await self._emit_progress(
                ProgressEventType.RETRIEVAL_STRATEGY,
                "completed",
                f"Using {strategy_response.assistant} assistant",
            )
            
        except Exception as error:
            await self._handle_error("Strategy selection", error)
            await self._set_default_retrieval_strategy()
            await self._emit_progress(
                ProgressEventType.RETRIEVAL_STRATEGY,
                "completed",
                "Using default search strategy"
            )
    
    @listen(determine_retrieval_strategy)
    async def fetch_documents(self) -> None:
        """Execute document retrieval using selected strategy and assistant translations."""        
        await self._emit_progress(
            ProgressEventType.DOCUMENT_RETRIEVAL,
            "in_progress",
            "Searching legal document database across multiple languages..."
        )
        
        try:
            strategy = self.state.retrieval_output.get("strategy", "hybrid")
            assistant = self.state.selected_assistant or "umumiy"
            self.state.rewritten_query = self.state.retrieval_output.get("query_rewrite", self.state.query)
            
            # Get multilingual translations from agent output
            query_translations = self.state.retrieval_output.get("query_translations", {})
            
            # Fallback to rewritten query if translations are missing
            if not query_translations:
                query_translations = {"original": self.state.rewritten_query}
            
            # Map assistant to collection name
            collection_name = settings.MILVUS_SOLIQ_ASSISTANT_NAME if assistant == "soliq" else settings.MILVUS_MAIN_NAME
            
            # If specific strategy, always use main collection
            if strategy == "specific":
                collection_name = settings.MILVUS_MAIN_NAME # because specific means main collection
            
            # Use multilingual retrieval
            documents_list = await self.retrieval_service.retrieve_multilingual(
                query_translations=query_translations,
                top_k=settings.TOP_K,
                search_type=strategy,
                collection_name=collection_name
            )
            
            # Format documents into string for state
            self.state.retrieval_docs = await self.retrieval_service._format_results(documents_list)
            
            await self._emit_progress(
                ProgressEventType.DOCUMENT_RETRIEVAL,
                "completed",
                f"Retrieved {len(documents_list)} unique highly relevant documents"
            )
            
        except Exception as error:
            await self._handle_error("Document retrieval", error)
            self.state.retrieval_docs = ""
            await self._emit_progress(
                ProgressEventType.DOCUMENT_RETRIEVAL,
                "failed",
                "Document retrieval failed, continuing..."
            )
    
    @router(fetch_documents)
    async def evaluate_context_sufficiency(self) -> str:
        """Evaluate if retrieved context is sufficient to answer the query."""        
        await self._emit_progress(
            ProgressEventType.CONTEXT_EVALUATION,
            "in_progress",
            "Evaluating document relevance and quality..."
        )
        
        try:
            # Build formatted input for evaluation crew
            evaluation_input = {
                "query": self.state.query,
                "context": self.state.retrieval_docs or "No context retrieved."
            }
            
            result = await self.evaluation_crew.kickoff_async(
                inputs=evaluation_input,
            )
            
            evaluation_response = await self._parse_structured_output(result, ContextEvaluationResponse)
            self.state.context_evaluation_output = evaluation_response.model_dump()
            
            if evaluation_response.reasoning:
                message = "Context is sufficient because " + evaluation_response.reasoning if evaluation_response.is_sufficient else \
                        "Context is insufficient because " + evaluation_response.reasoning
            else:
                message = "Context evaluation completed."
            
            await self._emit_progress(
                ProgressEventType.CONTEXT_EVALUATION,
                "completed",
                message
            )
            
            # If sufficient, go to answer generation
            # If insufficient, trigger parallel retry
            return 'sufficient' if evaluation_response.is_sufficient else 'insufficient'
            
        except Exception as error:
            await self._handle_error("Context evaluation", error)
            # Default to sufficient if evaluation fails
            self.state.context_evaluation_output = {
                "is_sufficient": True,
                "reasoning": "Evaluation failed, proceeding with available context",
                "missing_info": ""
            }
            await self._emit_progress(
                ProgressEventType.CONTEXT_EVALUATION,
                "completed",
                "Proceeding with available information"
            )
            return 'sufficient'

    @listen('insufficient')
    async def perform_web_search(self) -> None:
        """Perform web search in parallel when context is insufficient."""        
        await self._emit_progress(
            ProgressEventType.CONTEXT_EVALUATION,
            "in_progress",
            "Switching to web search for additional information..."
        )
        
        await self._emit_progress(
            ProgressEventType.WEB_SEARCH,
            "in_progress",
            "Searching the web for additional information..."
        )
        
        try:
            web_response = await self._execute_web_search()
            
            await self._merge_web_documents(web_response)

            message = await self._format_web_search_message(web_response)
            await self._emit_progress(
                ProgressEventType.WEB_SEARCH,
                "completed",
                message
            )
            
        except Exception as error:
            await self._handle_error("Web search", error)
            await self._emit_progress(
                ProgressEventType.WEB_SEARCH,
                "completed",
                "Proceeding without web results"
            )
    
    @listen(perform_web_search)
    async def generate_final_answer(self) -> str:
        """Generate comprehensive answer from all retrieved context."""        
        await self._emit_progress(
            ProgressEventType.ANSWER_GENERATION,
            "in_progress",
            "Generating comprehensive answer..."
        )
        
        try:
            agent_input = await self._build_final_agent_input()
            
            # According to assistant selection, use the corresponding answer crew
            assistant = self.state.selected_assistant or "umumiy"
            final_crew = self.answer_crews.get(assistant, self.answer_crews["umumiy"])
                
            streaming = await final_crew.kickoff_async(
                inputs=agent_input,
            )
            
            # Buffer to track if we're still in the prefix section
            chunk_buffer = ""
            prefix_complete = False
            
            async for chunk in streaming:
                if chunk.chunk_type == StreamChunkType.TEXT and self.progress_callback:
                    if not prefix_complete:
                        # Add to buffer
                        chunk_buffer += chunk.content
                        
                        # Check if we've passed the "Final Answer:" marker
                        if "Final Answer:" in chunk_buffer:
                            # Extract content after "Final Answer:"
                            parts = chunk_buffer.split("Final Answer:", 1)
                            if len(parts) > 1:
                                remaining_content = parts[1].lstrip()
                                prefix_complete = True
                                
                                # Send the remaining content if any
                                if remaining_content:
                                    await self.progress_callback({
                                        "type": "chunk",
                                        "chunk": remaining_content
                                    })
                                # Clear buffer
                                chunk_buffer = ""
                    else:
                        # Prefix already removed, stream directly
                        await self.progress_callback({
                            "type": "chunk",
                            "chunk": chunk.content
                        })
            
            self.state.answer = str(streaming.result)
              
        except Exception as error:
            await self._handle_error("Answer generation", error)
            self.state.answer = "Error generating answer. Please try again."
            await self._emit_progress(
                ProgressEventType.ANSWER_GENERATION,
                "failed",
                "Answer generation failed"
            )
        
        return self.state.answer
    
    @listen('sufficient')
    async def generate_final_answer_sufficient(self) -> str:
        """Generate answer when context is sufficient."""
        return await self.generate_final_answer()
        
    async def _fetch_memories(self) -> tuple[Any, Dict]:
        """Fetch session and personal memories concurrently."""
        session_memory = await self.memory_service.get_session_memory(
            user_id=self.state.user_id,
            session_id=self.state.session_id
        )
        personal_memory = await self.memory_service.get_all_memories(
            user_id=self.state.user_id
        )
        return session_memory, personal_memory
    
    async def _has_any_memory(self, session_memory: Any, personal_memory: Dict) -> bool:
        """Check if any memory exists."""
        has_session = bool(session_memory)
        has_personal = bool(personal_memory and personal_memory.get("memories"))
        return has_session or has_personal
    
    async def _summarize_memory(
        self,
        session_memory: Any,
        personal_memory: Dict
    ) -> MemoryAgentResponse:
        """Summarize memory using cached agent."""
        memory_input = await self._build_memory_input(session_memory, personal_memory)
        result = await self.memory_crew.kickoff_async(
            inputs=memory_input,
        )
        return await self._parse_structured_output(result, MemoryAgentResponse)
    
    async def _build_memory_input(self, session_memory: Any, personal_memory: Dict) -> Dict:
        """Build input for memory crew."""
        return {
            "query": self.state.query,
            "session_memory": json.dumps(session_memory, ensure_ascii=False),
            "personal_memory": json.dumps(personal_memory, ensure_ascii=False)
        }
    
    async def _apply_memory_response(self, memory_response: MemoryAgentResponse) -> None:
        """Apply memory response to state."""
        self.state.memory_output = memory_response.model_dump()
        self.state.memory_docs = await self._format_memory_context(memory_response)
        # Use original query if resolved_query is None
        self.state.resolved_query = memory_response.resolved_query or self.state.query
    
    async def _execute_web_search(self) -> WebSearchResponse:
        """Execute web search using web search crew and return structured response."""
        inputs = {"query": self.state.rewritten_query}
        result = await self.web_search_crew.kickoff_async(
            inputs=inputs,
        )
        web_response = await self._parse_structured_output(result, WebSearchResponse)
        
        self.state.web_search_output = web_response.model_dump()
        return web_response
    
    async def _merge_web_documents(self, web_response: WebSearchResponse) -> None:
        """Merge web documents into retrieval context."""
        combined_docs = self.state.retrieval_docs or ""
        combined_docs += "\n\n--- Web Search Results ---\n"
        
        for doc in web_response.docs:
            combined_docs += f"\nSource: {doc.url}\n"
            if doc.title:
                combined_docs += f"Title: {doc.title}\n"
            combined_docs += f"{doc.content}\n"
        
        self.state.retrieval_docs = combined_docs
        
    async def _format_web_search_message(self, web_response: WebSearchResponse) -> str:
        docs = web_response.docs
        if not docs:
            return "No relevant web documents found"

        sources = []
        for doc in docs:
            sources.append(f"Title: {doc.title}\n Url:({doc.url})\n Content: ({doc.content})\n\n")

        sources_str = "; ".join(sources)

        return (
            f"Found {len(docs)} web document(s) as additional sources: "
            f"{sources_str}"
        )
    
    async def _build_final_agent_input(self) -> Dict:
        """Build input for final answer crew."""
        return {
            "query": self.state.query,
            "resolved_query": f"{self.state.resolved_query}, Query which may clarify questions from memory.",
            "context": self.state.retrieval_docs or "No retrived text",
            "chat_history": self.state.memory_docs or "",
            "web_search": json.dumps(self.state.web_search_output, ensure_ascii=False) or ""
        }

    async def _parse_structured_output(self, output: Any, schema_class: type) -> Any:
        """Parse structured output using Pydantic schema."""
        output_str = output.raw if hasattr(output, 'raw') else str(output)
        parsed_dict = await self._parse_json_output(output_str)
        
        try:
            return schema_class(**parsed_dict)
        except Exception as error:
            logger.warning(f"Failed to parse into {schema_class.__name__}: {error}")
            return schema_class()
    
    async def _parse_json_output(self, output_str: str) -> Dict[str, Any]:
        """Parse JSON from string with fallback handling."""
        # Replace None/null with proper JSON null first
        output_str = output_str.replace(": None", ": null")
        
        if parsed := await self._try_direct_json_parse(output_str):
            return parsed
        
        if parsed := await self._try_extract_code_block(output_str):
            return parsed
        
        logger.warning(f"Could not parse JSON from output: {output_str[:200]}...")
        return {}
    
    async def _try_direct_json_parse(self, text: str) -> Dict[str, Any] | None:
        """Attempt direct JSON parsing."""
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            return None
    
    async def _try_extract_code_block(self, text: str) -> Dict[str, Any] | None:
        """Extract and parse JSON from markdown code blocks."""
        for delimiter in ["```json", "```"]:
            if delimiter not in text:
                continue
            
            try:
                json_str = text.split(delimiter)[1].split("```")[0].strip()
                return json.loads(json_str)
            except (IndexError, json.JSONDecodeError):
                continue
        
        return None
    
    async def _format_memory_context(self, memory_response: MemoryAgentResponse) -> str:
        """Format memory response for context inclusion."""
        return f"""
            Session Context:
            {memory_response.session_notes}

            User Preferences:
            {memory_response.personal_notes}
        """.strip()
    
    async def _set_default_memory(self) -> None:
        """Set default empty memory."""
        default_response = MemoryAgentResponse(
            session_notes="",
            personal_notes="",
            resolved_query=self.state.query
        )
        self.state.memory_output = default_response.model_dump()
        self.state.memory_docs = ""
        self.state.resolved_query = self.state.query
    
    async def _set_default_retrieval_strategy(self) -> None:
        """Set default retrieval strategy."""
        default_response = RetrievalStrategyResponse(
            strategy="hybrid",
            query_rewrite=self.state.query,
            assistant="umumiy"
        )
        self.state.retrieval_output = default_response.model_dump()
        self.state.selected_assistant = "umumiy"
    
    async def _handle_error(self, operation: str, error: Exception) -> None:
        """Log error and add to state."""
        logger.error(f"✗ {operation} failed: {error}", exc_info=True)
        self.state.errors.append(f"{operation} error: {str(error)}")
    
    async def _enrich_query_with_memory(self, base_query: str) -> str:
        """Enrich query with resolved memory context if available."""
        if not await self._should_enrich_query():
            return base_query
        
        return f"{base_query} Resolved Query from memory: {self.state.resolved_query}".strip()
    
    async def _should_enrich_query(self) -> bool:
        """Check if query should be enriched with memory context."""
        return (
            self.state.resolved_query and 
            self.state.resolved_query != self.state.query
        )