# app/agent/crew.py

from crewai.flow.flow import Flow, listen, router, start
from crewai import Crew, Process
from pydantic import BaseModel
from typing import Optional
import json
from app.core.logger import logger
from app.agent.memory import MemoryAgent, memory_task
from app.agent.retrieval import RetrievalAgent, retrieval_task
from app.agent.final_answer import FinalAnswerAgent, final_answer_task
from app.retrieval.retrieval_service import RetrievalService


retrieval_service = RetrievalService()

class LegalQAState(BaseModel):
    """State object for the legal QA flow"""
    query: str = ""
    user_id: str = ""
    session_id: str = "default"
    user_type: str = "lawyer"
    language_instruction: str = "Respond in the same language as the question"
    chat_history: Optional[str] = None
    
    # Intermediate outputs
    memory_output: Optional[dict] = None
    retrieval_output: Optional[dict] = None
    retrieval_docs: Optional[list] = None
    answer: Optional[str] = None
    
    # Error tracking
    errors: list = []


class LegalQAFlow(Flow[LegalQAState]):
    """
    CrewAI Flow for end-to-end legal QA with subtasks:
    Memory → Retrieval → Web Search → Web Extraction → System Prompt → Final Answer
    """
    
    @start()
    async def start_memory_retrieval(self):
        """Step 1: Retrieve session and personal memory"""
        logger.info("=== Starting Memory Retrieval ===")
        try:
            crew = Crew(
                agents=[MemoryAgent],
                tasks=[memory_task],
                process=Process.sequential,
                verbose=True
            )
            
            result = await crew.kickoff_async(
                inputs={
                    "query": self.state.query,
                    "user_id": self.state.user_id,
                    "session_id": self.state.session_id,
                }
            )
            
            # Parse JSON output
            self.state.memory_output = await self._parse_json_output(result)
            logger.info(f"Memory output: {self.state.memory_output}")
            
        except Exception as e:
            logger.error(f"Memory retrieval failed: {e}")
            self.state.errors.append(f"Memory retrieval error: {str(e)}")
            self.state.memory_output = {
                "session_notes": "",
                "personal_notes": "",
            }
    
    @listen(start_memory_retrieval)
    async def start_retrieval(self):
        """Step 2: Retrieve legal documents using chosen strategy"""
        logger.info("=== Starting Document Retrieval ===")
        try:
            crew = Crew(
                agents=[RetrievalAgent],
                tasks=[retrieval_task],
                process=Process.sequential,
                verbose=True
            )
            
            result = await crew.kickoff_async(
                inputs={
                    "query": self.state.query,
                }
            )
            
            # Parse JSON output
            self.state.retrieval_output = await self._parse_json_output(result)
            logger.info(f"Retrieval strategy: {self.state.retrieval_output.get('strategy')}")
            
        except Exception as e:
            logger.error(f"Retrieval failed: {e}")
            self.state.errors.append(f"Retrieval error: {str(e)}")
            self.state.retrieval_output = {
                "query_rewrite": self.state.query,
                "strategy": "hybrid",
            }
            
    @listen(start_retrieval)
    async def retreive_documents(self):
        """Step 3: Retrieve documents if needed"""
        logger.info("=== Retrieving Documents if Needed ===")
        try:
            strategy = self.state.retrieval_output.get("strategy", "hybrid")
            query_rewrite = self.state.retrieval_output.get("query_rewrite", self.state.query)
            
            result = await retrieval_service.retrieve_context(
                query=query_rewrite,
                search_type=strategy
            )
            
            self.state.retrieval_docs = str(result)
            
            logger.info(f"Retrieved documents updated in retrieval output.")
                
        except Exception as e:
            logger.error(f"Document retrieval failed: {e}")
            self.state.errors.append(f"Document retrieval error: {str(e)}")
            self.state.retrieval_output = {
                "query_rewrite": self.state.query,
                "strategy": "hybrid",
            }
    
    @listen(retreive_documents)
    async def generate_final_answer(self):
        """Step 6: Generate the final answer"""
        logger.info("=== Generating Final Answer ===")
        try:            
            crew = Crew(
                agents=[FinalAnswerAgent],
                tasks=[final_answer_task],
                process=Process.sequential,
                verbose=True
            )
            
            result = await crew.kickoff_async(
                inputs={
                    "query": self.state.query,
                    "context": self.state.retrieval_docs or "",
                    "chat_history": json.dumps(self.state.memory_output) if self.state.memory_output else "{}",
                    "user_type": self.state.user_type,
                    "language_instruction": self.state.language_instruction,
                }
            )
            
            logger.info(f"Final answer output: {result}")
            
            self.state.answer = str(result)
            logger.info("Final answer generated successfully")
            
        except Exception as e:
            logger.error(f"Final answer generation failed: {e}")
            self.state.errors.append(f"Final answer error: {str(e)}")
            self.state.answer = "Error generating answer. Please try again."
    
    async def _parse_json_output(self, output) -> dict:
        """
        Parse JSON from agent output.
        Handles cases where the output might be wrapped in markdown or contain extra text.
        """
        # Handle CrewOutput objects
        if hasattr(output, 'raw'):
            output_str = output.raw
        else:
            output_str = str(output)
            
        try:
            # Try direct JSON parsing
            return json.loads(output_str)
        except json.JSONDecodeError:
            # Try extracting JSON from markdown code blocks
            if "```json" in output_str:
                json_str = output_str.split("```json")[1].split("```")[0].strip()
                return json.loads(json_str)
            elif "```" in output_str:
                json_str = output_str.split("```")[1].split("```")[0].strip()
                return json.loads(json_str)
            else:
                logger.warning(f"Could not parse JSON from output: {output_str[:100]}")
                return {}


# Async function to run the flow
async def run_legal_qa_flow(
    query: str,
    user_id: str = "user_123",
    session_id: str = "default",
    user_type: str = "lawyer",
    language_instruction: str = "Respond in the same language as the question",
    chat_history: Optional[str] = None,
    **kwargs
) -> dict:
    """
    Run the legal QA flow end-to-end.
    
    Args:
        query: The user's legal question
        user_id: User identifier
        session_id: Session identifier
        user_type: Type of user (general, legal_professional, etc.)
        language_instruction: Language instruction for response
        chat_history: Previous chat history context
        **kwargs: Additional state variables
    
    Returns:
        dict with final_answer, state details, and any errors
    """
    # Create initial state
    initial_state = {
        "query": query,
        "user_id": user_id,
        "session_id": session_id,
        "user_type": user_type,
        "language_instruction": language_instruction,
        "chat_history": chat_history,
        **kwargs,
    }
    
    # Create and run flow
    
    
    
    flow = LegalQAFlow()
    result = await flow.kickoff_async(initial_state)
    
    return result


# For backwards compatibility
crew = None  # Remove default instance since Flow needs to be instantiated with state