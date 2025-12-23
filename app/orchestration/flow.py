# app/orchestration/flow.py

import json
from typing import Any, Dict

from crewai.flow.flow import Flow, listen, start, router, and_

from app.orchestration.agents import Agents
from app.orchestration.schemas import (
    AgenticRAGState,
    MemoryAgentResponse,
    RetrievalStrategyResponse,
    ContextEvaluationResponse,
    WebSearchResponse
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
    
    Streaming is enabled by default to provide real-time output from crew executions.
    """
    
    stream = True  # Enable streaming for all crew executions
    
    def __init__(self, enable_progress_stream: bool = False):
        super().__init__(tracing=settings.TRACING)
        self.enable_progress_stream = enable_progress_stream
        self._initialize_services()
        self._initialize_agents()
    
    def _initialize_services(self) -> None:
        """Initialize all required services."""
        self.retrieval_service = RetrievalService()
        self.memory_service = ChatMemoryService()
    
    def _initialize_agents(self) -> None:
        """Initialize and cache all agents once."""
        agents_factory = Agents()
        self.memory_agent = agents_factory.memory_summarizer()
        self.retrieval_agent = agents_factory.retrieval_specialist()
        self.context_evaluator_agent = agents_factory.context_evaluator()
        self.web_search_agent = agents_factory.web_search_summarizer()
        self.final_answer_agents = {
            "umumiy": agents_factory.final_answer_umumiy(),
            "soliq": agents_factory.final_answer_soliq(),
        }
    
    @start()
    async def start_memory_retrieval(self) -> None:
        """Retrieve and summarize session and personal memory."""
        logger.info("=== Step 1: Memory Retrieval ===")
        try:
            session_memory, personal_memory = await self._fetch_memories()
            
            if not await self._has_any_memory(session_memory, personal_memory):
                logger.info("No memories found; skipping summarization.")
                await self._set_default_memory()
                return
            
            memory_response = await self._summarize_memory(session_memory, personal_memory)
            await self._apply_memory_response(memory_response)
            
            self.state.enriched_query = await self._enrich_query_with_memory(self.state.query)
            logger.info(f"✓ Memory retrieved: {len(self.state.memory_docs)} characters")
            
        except Exception as error:
            await self._handle_error("Memory retrieval", error)
            await self._set_default_memory()
    
    @listen(start_memory_retrieval)
    async def determine_retrieval_strategy(self) -> None:
        """Analyze query to select optimal retrieval strategy."""
        logger.info("=== Step 2: Retrieval Strategy Selection ===")
        
        try:
            # Build query input for retrieval agent
            query_input = self.state.enriched_query or self.state.query
            result = await self.retrieval_agent.kickoff_async(
                query_input,
                response_format=RetrievalStrategyResponse
            )
            strategy_response = await self._parse_structured_output(result, RetrievalStrategyResponse)
            
            self.state.retrieval_output = strategy_response.model_dump()
            self.state.selected_assistant = strategy_response.assistant
            
            logger.info(f"✓ Strategy: {strategy_response.strategy}, Assistant: {strategy_response.assistant}")
            logger.info(f"  Query rewrite: {strategy_response.query_rewrite}")
            if strategy_response.reasoning:
                logger.info(f"  Reasoning: {strategy_response.reasoning}")
            
        except Exception as error:
            await self._handle_error("Strategy selection", error)
            await self._set_default_retrieval_strategy()
    
    @listen(determine_retrieval_strategy)
    async def fetch_documents(self) -> None:
        """Execute document retrieval using selected strategy and assistant."""
        logger.info("=== Step 3: Document Retrieval ===")
        
        try:
            strategy = self.state.retrieval_output.get("strategy", "hybrid")
            assistant = self.state.selected_assistant or "umumiy"
            self.state.rewritten_query = self.state.retrieval_output.get("query_rewrite", self.state.query)
            
            # Map assistant to collection name
            collection_name = settings.MILVUS_SOLIQ_ASSISTANT_NAME if assistant == "soliq" else settings.MILVUS_MAIN_NAME
            
            # If specific strategy, always use main collection
            if strategy == "specific":
                collection_name = settings.MILVUS_MAIN_NAME # because specific means main collection
            
            documents = await self.retrieval_service.retrieve_context(
                query=self.state.rewritten_query,
                search_type=strategy,
                collection_name=collection_name
            )
            
            self.state.retrieval_docs = str(documents)
            logger.info(f"✓ Documents retrieved: {len(self.state.retrieval_docs)} characters from {assistant} assistant")
            
        except Exception as error:
            await self._handle_error("Document retrieval", error)
            self.state.retrieval_docs = ""
    
    @router(fetch_documents)
    async def evaluate_context_sufficiency(self) -> str:
        """Evaluate if retrieved context is sufficient to answer the query."""
        logger.info("=== Step 4: Context Evaluation ===")
        
        try:
            # Build formatted input for context evaluator
            evaluation_input = f"""
            Query: {self.state.query}

            Retrieved Context:
            {self.state.retrieval_docs or "No context retrieved."}
            """
            
            result = await self.context_evaluator_agent.kickoff_async(
                evaluation_input,
                response_format=ContextEvaluationResponse
            )
            
            evaluation_response = await self._parse_structured_output(result, ContextEvaluationResponse)
            self.state.context_evaluation_output = evaluation_response.model_dump()
            
            logger.info(f"✓ Context sufficient: {evaluation_response.is_sufficient}")
            
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
            return 'sufficient'
    
    @listen('insufficient')
    async def retry_query_and_retrieval(self) -> None:
        """Retry with improved query rewriting and retrieval when context is insufficient."""
        logger.info("=== Step 5a: Retry Query & Retrieval (Parallel) ===")
        
        try:
            await self.determine_retrieval_strategy()
            await self.fetch_documents()
            logger.info(f"✓ Retry retrieval completed characters")
            
        except Exception as error:
            await self._handle_error("Retry query and retrieval", error)
    
    @listen('insufficient')
    async def perform_web_search(self) -> None:
        """Perform web search in parallel when context is insufficient."""
        logger.info("=== Step 5b: Web Search (Parallel) ===")
        try:
            web_response = await self._execute_web_search()
            
            if not web_response.docs:
                logger.info("No web search documents found.")
                return
            
            await self._merge_web_documents(web_response)
            logger.info(f"✓ Web search completed: {len(web_response.docs)} documents extracted")
            
        except Exception as error:
            await self._handle_error("Web search", error)
    
    @listen(and_(retry_query_and_retrieval, perform_web_search))
    async def generate_final_answer(self) -> str:
        """Generate comprehensive answer from all retrieved context."""
        logger.info("=== Step 6: Answer Generation ===")
        
        try:
            agent_input = await self._build_final_agent_input()
            
            # According to assistant selection, change goal of the final answer agent
            assistant = self.state.selected_assistant or "umumiy"
            final_agent = self.final_answer_agents.get(assistant, self.final_answer_agents["umumiy"])
                
            
            result = await final_agent.kickoff_async(agent_input)
            
            self.state.answer = str(result)
            logger.info(f"✓ Answer generated: {len(self.state.answer)} characters")
              
        except Exception as error:
            await self._handle_error("Answer generation", error)
            self.state.answer = "Error generating answer. Please try again."
        
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
        result = await self.memory_agent.kickoff_async(
            memory_input,
            response_format=MemoryAgentResponse
        )
        return await self._parse_structured_output(result, MemoryAgentResponse)
    
    async def _build_memory_input(self, session_memory: Any, personal_memory: Dict) -> str:
        """Build input for memory summarizer agent."""
        return f"""
            "query": "{self.state.query}",
            "session_memory": {json.dumps(session_memory, ensure_ascii=False)},
            "personal_memory": {json.dumps(personal_memory, ensure_ascii=False)},
        """
    
    async def _apply_memory_response(self, memory_response: MemoryAgentResponse) -> None:
        """Apply memory response to state."""
        self.state.memory_output = memory_response.model_dump()
        self.state.memory_docs = await self._format_memory_context(memory_response)
        # Use original query if resolved_query is None
        self.state.resolved_query = memory_response.resolved_query or self.state.query
    
    async def _execute_web_search(self) -> WebSearchResponse:
        """Execute web search using cached agent and return structured response."""
        result = await self.web_search_agent.kickoff_async(self.state.rewritten_query, response_format=WebSearchResponse)
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
    
    async def _build_final_agent_input(self) -> str:
        """Build input for final answer agent."""
        return f"""
            "query": "{self.state.query}",
            "resolved_query": "{self.state.resolved_query}, Query which may clarify questions from memory.",
            "context": {json.dumps(self.state.retrieval_docs or "", ensure_ascii=False)},
            "chat_history": {json.dumps(self.state.memory_docs or "", ensure_ascii=False)},
            "web_search": {json.dumps(self.state.web_search_output or {}, ensure_ascii=False)}
        """
    
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