# app/agent/crew.py

from typing import Optional, Dict, Any, List
import json
from crewai.flow.flow import Flow, listen, start
from pydantic import BaseModel, Field

# Import crews
from app.agent.crews.memory import memory_crew
from app.agent.crews.retrieval import retrieval_crew
from app.agent.crews.final_answer import final_answer_crew
from app.agent.crews.web_search import web_search_crew

# Import services
from app.retrieval.retrieval_service import RetrievalService
from app.core.logger import logger


class LegalQAState(BaseModel):
    """State management for legal QA workflow"""
    
    # Input parameters
    query: str = Field(default="", description="User's legal question")
    user_id: str = Field(default="", description="User identifier")
    session_id: str = Field(default="default", description="Session identifier")
    user_type: str = Field(default="lawyer", description="User type (lawyer, general, etc.)")
    language_instruction: str = Field(
        default="Respond in the same language as the question",
        description="Language instruction for response"
    )
    enable_web_search: bool = Field(default=True, description="Flag to enable/disable web search fallback")
    chat_history: Optional[str] = Field(default=None, description="Previous chat context")
    
    # Intermediate outputs
    memory_output: Optional[Dict[str, Any]] = Field(default=None, description="Structured memory data")
    memory_docs: Optional[str] = Field(default=None, description="Formatted memory for context")
    retrieval_output: Optional[Dict[str, Any]] = Field(default=None, description="Retrieval metadata")
    retrieval_docs: Optional[str] = Field(default=None, description="Retrieved document content")
    web_search_output: Optional[Dict[str, Any]] = Field(default=None, description="Web search results")
    web_extraction_output: Optional[Dict[str, Any]] = Field(default=None, description="Web extraction results")
    
    # Final output
    answer: Optional[str] = Field(default=None, description="Generated answer")
    
    # Error tracking
    errors: List[str] = Field(default_factory=list, description="Accumulated errors")

    class Config:
        arbitrary_types_allowed = True


class LegalQAFlow(Flow[LegalQAState]):
    """
    Multi-agent legal QA flow with sequential execution:
    1. Memory Retrieval: Fetch session and personal memory
    2. Document Retrieval Strategy: Determine best retrieval approach
    3. Document Fetch: Execute retrieval from vector DB
    4. Final Answer: Generate response using LLM
    """
    
    def __init__(self):
        super().__init__()
        self.retrieval_service = RetrievalService()
    
    @start()
    async def start_memory_retrieval(self) -> None:
        """
        Step 1: Retrieve session and personal memory
        
        Fetches:
        - Session notes: Recent conversation context
        - Personal notes: Long-term user preferences and information
        """
        logger.info("=== Step 1: Memory Retrieval ===")
        
        try:
            result = await memory_crew.kickoff_async(
                inputs={
                    "query": self.state.query,
                    "user_id": self.state.user_id,
                    "session_id": self.state.session_id,
                }
            )
            
            # Parse and structure memory output
            self.state.memory_output = self._parse_json_output(result)
            self.state.memory_docs = self._format_memory_context(self.state.memory_output)
            
            logger.info(f"✓ Memory retrieved: {len(self.state.memory_docs)} characters")
            
        except Exception as e:
            logger.error(f"✗ Memory retrieval failed: {e}", exc_info=True)
            self.state.errors.append(f"Memory retrieval error: {str(e)}")
            self._set_default_memory()
    
    @listen(start_memory_retrieval)
    async def determine_retrieval_strategy(self) -> None:
        """
        Step 2: Determine optimal retrieval strategy
        
        Agent analyzes query to choose:
        - hybrid: Combines semantic + keyword search
        - dense: Semantic similarity search
        - sparse: Keyword/BM25 search
        - specific: Exact article/statute lookup
        """
        logger.info("=== Step 2: Retrieval Strategy Selection ===")
        
        try:
            result = await retrieval_crew.kickoff_async(
                inputs={"query": self.state.query}
            )
            
            self.state.retrieval_output = self._parse_json_output(result)
            strategy = self.state.retrieval_output.get("strategy", "hybrid")
            query_rewrite = self.state.retrieval_output.get("query_rewrite", self.state.query)
            
            logger.info(f"✓ Strategy selected: {strategy}")
            logger.info(f"  Query rewrite: {query_rewrite}")
            
        except Exception as e:
            logger.error(f"✗ Strategy selection failed: {e}", exc_info=True)
            self.state.errors.append(f"Retrieval strategy error: {str(e)}")
            self._set_default_retrieval_strategy()
    
    @listen(determine_retrieval_strategy)
    async def fetch_documents(self) -> None:
        """
        Step 3: Execute document retrieval
        
        Fetches relevant legal documents from vector database
        using the strategy determined in previous step
        """
        logger.info("=== Step 3: Document Retrieval ===")
        
        try:
            strategy = self.state.retrieval_output.get("strategy", "hybrid")
            query = self.state.retrieval_output.get("query_rewrite", self.state.query)
            
            result = await self.retrieval_service.retrieve_context(
                query=query,
                search_type=strategy
            )
            
            self.state.retrieval_docs = str(result)
            logger.info(f"✓ Documents retrieved: {len(self.state.retrieval_docs)} characters")
            
        except Exception as e:
            logger.error(f"✗ Document retrieval failed: {e}", exc_info=True)
            self.state.errors.append(f"Document fetch error: {str(e)}")
            self.state.retrieval_docs = ""
            
    @listen(fetch_documents)
    async def perform_web_search(self) -> None:
        """
        Step 4: Web search and extraction
        """
        logger.info("=== Step 4: Conditional Web Search ===")
        
        try:
            if self.state.enable_web_search is False:
                logger.info("Web search disabled by configuration.")
                return
            
            # Execute web search crew
            result = await web_search_crew.kickoff_async(
                inputs={
                    "query": self.state.query,
                }
            )
            
            # Parse outputs
            web_search_json = self._parse_json_output(result)
            
            self.state.web_search_output = web_search_json
            
            # Combine extracted docs into retrieval docs
            extracted_docs = web_search_json.get("docs", [])
            combined_docs = self.state.retrieval_docs or ""
            combined_docs += "\n\n--- Web Search Results ---\n"
            for doc in extracted_docs:
                content = doc.get("content", "")
                url = doc.get("url", "")
                str_doc = f"\nSource: {url}\n{content}\n"
                
                combined_docs += str_doc      
            
            self.state.retrieval_docs = combined_docs
            
            logger.info(f"✓ Web search and extraction completed: {len(extracted_docs)} documents")
            
        except Exception as e:
            logger.error(f"✗ Web search/extraction failed: {e}", exc_info=True)
            self.state.errors.append(f"Web search error: {str(e)}")
    
    @listen(perform_web_search)
    async def generate_final_answer(self) -> None:
        """
        Step 4: Generate final answer
        
        Synthesizes all retrieved information to produce
        a comprehensive, contextually-aware response
        """
        logger.info("=== Step 4: Final Answer Generation ===")
        
        try:
            result = await final_answer_crew.kickoff_async(
                inputs={
                    "query": self.state.query,
                    "context": self.state.retrieval_docs or "",
                    "chat_history": self.state.memory_docs or "",
                    "user_type": self.state.user_type,
                    "language_instruction": self.state.language_instruction,
                }
            )
            
            self.state.answer = str(result)
            logger.info(f"✓ Answer generated: {len(self.state.answer)} characters")
            
        except Exception as e:
            logger.error(f"✗ Answer generation failed: {e}", exc_info=True)
            self.state.errors.append(f"Final answer error: {str(e)}")
            self.state.answer = "Error generating answer. Please try again."
    
    # Helper Methods  
    def _parse_json_output(self, output: Any) -> Dict[str, Any]:
        """
        Parse JSON from agent output with fallback handling
        
        Handles:
        - Direct JSON strings
        - CrewOutput objects with .raw attribute
        - Markdown code blocks (```json ... ```)
        - Plain code blocks (``` ... ```)
        """
        # Extract string representation
        output_str = output.raw if hasattr(output, 'raw') else str(output)
        
        # Try direct parsing
        try:
            return json.loads(output_str)
        except json.JSONDecodeError:
            pass
        
        # Try markdown code block extraction
        for delimiter in ["```json", "```"]:
            if delimiter in output_str:
                try:
                    json_str = output_str.split(delimiter)[1].split("```")[0].strip()
                    return json.loads(json_str)
                except (IndexError, json.JSONDecodeError):
                    continue
        
        # Fallback
        logger.warning(f"Could not parse JSON from output: {output_str[:200]}...")
        return {}
    
    def _format_memory_context(self, memory_output: Dict[str, Any]) -> str:
        """Format memory output for context inclusion"""
        session_notes = memory_output.get('session_notes', '')
        personal_notes = memory_output.get('personal_notes', '')
        
        return f"""
        Session Context:
        {session_notes}

        User Preferences:
        {personal_notes}
        """.strip()
    
    def _set_default_memory(self) -> None:
        """Set default empty memory on failure"""
        self.state.memory_output = {
            "session_notes": "",
            "personal_notes": "",
        }
        self.state.memory_docs = ""
    
    def _set_default_retrieval_strategy(self) -> None:
        """Set default retrieval strategy on failure"""
        self.state.retrieval_output = {
            "query_rewrite": self.state.query,
            "strategy": "hybrid",
        }

# Main Function
async def run_agentic_rag(
    query: str,
    user_id: str = "user_123",
    session_id: str = "default",
    user_type: str = "lawyer",
    language_instruction: str = "Respond in the same language as the question",
    chat_history: Optional[str] = None,
    **kwargs
) -> LegalQAState:
    """
    Execute legal QA flow end-to-end
    
    Args:
        query: User's legal question
        user_id: User identifier for personalization
        session_id: Session identifier for context continuity
        user_type: User type (lawyer, general, etc.) for response tailoring
        language_instruction: Language preference for response
        chat_history: Previous conversation context
        **kwargs: Additional state parameters
    
    Returns:
        LegalQAState: Complete state object with answer and metadata
    """
    logger.info(f"Starting Legal QA Flow for query: {query[:100]}...")
    
    # Build initial state
    initial_state = {
        "query": query,
        "user_id": user_id,
        "session_id": session_id,
        "user_type": user_type,
        "language_instruction": language_instruction,
        "chat_history": chat_history,
        **kwargs,
    }
    
    # Execute flow
    flow = LegalQAFlow()
    result_state = await flow.kickoff_async(initial_state)
    
    return result_state