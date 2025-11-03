# app/agent/crew.py

import os
from typing import Dict, Any
import json
from crewai.flow.flow import Flow, listen, start

# Import crews
from app.agent.crews.memory import memory_crew
from app.agent.crews.retrieval import retrieval_crew
from app.agent.crews.final_answer import final_answer_crew
from app.agent.crews.web_search import web_search_crew

# Import state
from app.agent.state import AgenticRAGState

# Import services
from app.retrieval.retrieval_service import RetrievalService
from app.services.language_service import LanguageDetector
from app.core.logger import logger
from app.core.config import settings


class AgenticRAGFlow(Flow[AgenticRAGState]):
    """
    Agentic RAG flow with sequential execution:
    1. Memory Retrieval: Fetch session and personal memory
    2. Document Retrieval Strategy: Determine best retrieval approach
    3. Document Fetch: Execute retrieval from vector DB
    4. Web Search & Extraction if enabled
    5. Final Answer: Generate response using LLM
    """
    
    def __init__(self):
        super().__init__(tracing=settings.TRACING)
        self.retrieval_service = RetrievalService()
        self.language_service = LanguageDetector()
        
    @start()
    async def detect_language_instruction(self) -> None:
        """
        Step 0: Detect language of the query
        
        Sets language_instruction in state for later use
        """
        logger.info("=== Step 0: Language Detection ===")
        
        try:
            lang = self.language_service.detect_language(self.state.query)
            
            self.state.query_language = lang
            self.state.language_instruction = self.language_service.get_instruction(lang)
            
            logger.info(f"✓ Detected language: {lang}")
            
        except Exception as e:
            logger.error(f"✗ Language detection failed: {e}", exc_info=True)
            self.state.errors.append(f"Language detection error: {str(e)}")
            self.state.language_instruction = "Respond in the same language as the question."
        
    
    @listen(detect_language_instruction)
    async def start_memory_retrieval(self) -> None:
        """
        Step 1: Retrieve session and personal memory
        
        Fetches:
        - Session notes: Recent conversation context
        - Personal notes: Long-term user preferences and information
        """
        logger.info("=== Step 1: Memory Retrieval ===")
        
        try:
            if self.state.enable_memory is False:
                logger.info("Memory retrieval disabled by configuration.")
                self._set_default_memory()
                return
            
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
        
    @listen(generate_final_answer)
    async def correct_answer_language(self) -> None:
        """
        Step 5: Language correction of final answer
        
        Ensures the final answer matches the detected language of the query
        """
        logger.info("=== Step 5: Language Correction ===")
        
        try:
            if not self.state.query_language:
                logger.info("No detected query language; skipping correction.")
                self.state.language_corrected_answer = self.state.answer
                return
            
            corrected_answer = self.language_service.correct_language(
                text=self.state.answer,
                language=self.state.query_language
            )
            
            self.state.language_corrected_answer = corrected_answer
            logger.info("✓ Language correction completed.")
            logger.info('Final corrected answer preview: ' + corrected_answer)
            
            return corrected_answer
            
        except Exception as e:
            logger.error(f"✗ Language correction failed: {e}", exc_info=True)
            self.state.errors.append(f"Language correction error: {str(e)}")
            self.state.language_corrected_answer = self.state.answer
            
            return self.state.answer
    
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
