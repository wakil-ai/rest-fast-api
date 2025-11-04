# app/orchestration/flow.py

import json
from typing import Any, Dict

from crewai.flow.flow import Flow, listen, start

from app.orchestration.agents import Agents
from app.orchestration.schemas import (
    AgenticRAGState,
    MemoryAgentResponse,
    RetrievalStrategyResponse,
    WebSearchResponse
)
from app.retrieval.retrieval_service import RetrievalService
from app.services.language_service import LanguageDetector
from app.services.memory_service import ChatMemoryService
from app.core.config import settings
from app.core.logger import logger


class AgenticRAGFlow(Flow[AgenticRAGState]):
    """
    Agentic RAG pipeline with memory, retrieval strategy, and web search.
    
    Flow:
        1. Language Detection
        2. Memory Retrieval (session + personal context)
        3. Retrieval Strategy Selection
        4. Document Retrieval
        5. Web Search (conditional)
        6. Answer Generation
        7. Language Correction
    """
    
    def __init__(self):
        super().__init__(tracing=settings.TRACING)
        self._initialize_services()
        self._initialize_agents()
    
    def _initialize_services(self) -> None:
        """Initialize all required services."""
        self.retrieval_service = RetrievalService()
        self.language_service = LanguageDetector()
        self.memory_service = ChatMemoryService()
    
    def _initialize_agents(self) -> None:
        """Initialize and cache all agents once."""
        agents_factory = Agents()
        self.memory_agent = agents_factory.memory_summarizer()
        self.retrieval_agent = agents_factory.retrieval_specialist()
        self.web_search_agent = agents_factory.web_search_summarizer()
        self.final_answer_agent = agents_factory.final_answer()
    
    @start()
    async def detect_language_instruction(self) -> None:
        """Detect query language and set response instruction."""
        logger.info("=== Step 0: Language Detection ===")
        
        try:
            detected_language = self.language_service.detect_language(self.state.query)
            self.state.query_language = detected_language
            self.state.language_instruction = self.language_service.get_instruction(detected_language)
            
            logger.info(f"✓ Detected language: {detected_language}")
            
        except Exception as error:
            await self._handle_error("Language detection", error)
            self.state.language_instruction = "Respond in the same language as the question."
    
    @listen(detect_language_instruction)
    async def start_memory_retrieval(self) -> None:
        """Retrieve and summarize session and personal memory."""
        logger.info("=== Step 1: Memory Retrieval ===")
        
        if not self.state.enable_memory:
            logger.info("Memory retrieval disabled by configuration.")
            await self._set_default_memory()
            return
        
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
            result = await self.retrieval_agent.kickoff_async(self.state.enriched_query)
            strategy_response = await self._parse_structured_output(result, RetrievalStrategyResponse)
            
            self.state.retrieval_output = strategy_response.model_dump()
            
            logger.info(f"✓ Strategy: {strategy_response.strategy}")
            logger.info(f"  Query rewrite: {strategy_response.query_rewrite}")
            if strategy_response.reasoning:
                logger.info(f"  Reasoning: {strategy_response.reasoning}")
            
        except Exception as error:
            await self._handle_error("Strategy selection", error)
            await self._set_default_retrieval_strategy()
    
    @listen(determine_retrieval_strategy)
    async def fetch_documents(self) -> None:
        """Execute document retrieval using selected strategy."""
        logger.info("=== Step 3: Document Retrieval ===")
        
        try:
            strategy = self.state.retrieval_output.get("strategy", "hybrid")
            self.state.rewritten_query = self.state.retrieval_output.get("query_rewrite", self.state.query)
            
            documents = await self.retrieval_service.retrieve_context(
                query=self.state.rewritten_query,
                search_type=strategy
            )
            
            self.state.retrieval_docs = str(documents)
            logger.info(f"✓ Documents retrieved: {len(self.state.retrieval_docs)} characters")
            
        except Exception as error:
            await self._handle_error("Document retrieval", error)
            self.state.retrieval_docs = ""
    
    @listen(fetch_documents)
    async def perform_web_search(self) -> None:
        """Perform web search and merge results with retrieved documents."""
        logger.info("=== Step 4: Web Search ===")
        
        if not self.state.enable_web_search:
            logger.info("Web search disabled by configuration.")
            return
        
        try:
            web_response = await self._execute_web_search()
            
            if not web_response.docs:
                logger.info("No web search documents found.")
                return
            
            await self._merge_web_documents(web_response)
            logger.info(f"✓ Web search completed: {len(web_response.docs)} documents extracted")
            
        except Exception as error:
            await self._handle_error("Web search", error)
    
    @listen(perform_web_search)
    async def generate_final_answer(self) -> None:
        """Generate comprehensive answer from all retrieved context."""
        logger.info("=== Step 5: Answer Generation ===")
        
        try:
            agent_input = await self._build_final_agent_input()
            result = await self.final_answer_agent.kickoff_async(agent_input)
            
            self.state.answer = str(result)
            logger.info(f"✓ Answer generated: {len(self.state.answer)} characters")
            
        except Exception as error:
            await self._handle_error("Answer generation", error)
            self.state.answer = "Error generating answer. Please try again."
    
    @listen(generate_final_answer)
    async def correct_answer_language(self) -> str:
        """Ensure answer matches detected query language."""
        logger.info("=== Step 6: Language Correction ===")
        
        if not self.state.query_language:
            logger.info("No detected language; skipping correction.")
            self.state.language_corrected_answer = self.state.answer
            return self.state.answer
        
        try:
            corrected_answer = self.language_service.correct_language(
                text=self.state.answer,
                language=self.state.query_language
            )
            
            self.state.language_corrected_answer = corrected_answer
            logger.info("✓ Language correction completed.")
            logger.info(f"Final answer preview: {corrected_answer[:100]}...")
            
            return corrected_answer
            
        except Exception as error:
            await self._handle_error("Language correction", error)
            self.state.language_corrected_answer = self.state.answer
            return self.state.answer
    
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
        self.state.resolved_query = memory_response.resolved_query
    
    async def _execute_web_search(self) -> WebSearchResponse:
        """Execute web search using cached agent and return structured response."""
        result = await self.web_search_agent.kickoff_async(self.state.rewritten_query)
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
            "user_type": {self.state.user_type},
            "language_instruction": {self.state.language_instruction},
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
            query_rewrite=self.state.query
        )
        self.state.retrieval_output = default_response.model_dump()
    
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