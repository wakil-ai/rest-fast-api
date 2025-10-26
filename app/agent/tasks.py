# app/agent/tasks.py

from crewai import Task
from app.agent.agents import RetrievalAgent, MemoryAgent
from app.agent.agents import FinalAnswerAgent
from app.agent.tools import (tools, RetrievalTool, 
                             SessionMemoryTool, PersonalMemoryTool, 
                             WebSearchTool, WebExtractionTool,
                             )
from app.chains.prompts import PROMPT

RetrievalTask = Task(
    description="""
    Tasks:
    1. Rewrite the user's query to optimize for document retrieval.
    2. Classify the retrieval strategy to use (hybrid/dense/sparse/specific).
    3. Retrieve the top relevant documents using the classified strategy.
    4. Return the retrieved context formatted as legal documents.
    
    Here is brief explanation of each retrieval type:
    - hybrid: Combines dense vector search (semantic embeddings) and sparse vector search (BM25 keyword relevance) for balanced results.
    - dense: Uses semantic embeddings to find conceptually similar documents based on meaning.
    - sparse: Uses keyword matching (BM25) to find documents with exact term matches.
    - specific: Uses targeted search for very specific queries with article numbers. For using it there must be article number in the query.
      - Example: "According to Article 123 of the Civil Code, what are the obligations of the parties in a contract?"
      
    User Question:
    {query}
    """,
    expected_output="""None""",
    agent=RetrievalAgent,
    tools=[RetrievalTool(), WebSearchTool, WebExtractionTool],
)

MemoryTask = Task(
    description="""Determine if session/personal memory is needed and retrieve it.
    
    User_id:
    {user_id} # For memory retrieval and personalization.

    Session_id:
    {session_id} # For session memory retrieval.
    
    Query:
    {query} # Current user query to check relevance against memory and to decide if memory is needed.
    """,
    expected_output="""
    Return previous conversation history formatted as:
    Previous Conversation:
    Q: [previous question]
    A: [previous answer]
    
    Relevant Past Memories:
    - [memory item 1]
    - [memory item 2]
    
    Return 'No relevant memory found' if there's no relevant past conversation.
    """,
    agent=MemoryAgent,
    tools=[SessionMemoryTool(), PersonalMemoryTool()],
)

FinalAnswerTask = Task(
    description="""
    You are an advanced AI Legal Information Assistant and Advisor.
    Generate a comprehensive legal answer using the retrieved context and memory from previous tasks.
    
    User Question: {query}
    User Type: {user_type}
    Language Instruction: {language_instruction}
    
    IMPORTANT: 
    - The context from RetrievalTask contains the retrieved legal documents.
    - The context from MemoryTask contains session and personal memory.
    - Use this context to answer the user's question following the SYSTEM PROMPT rules.
    
    Tasks:
    1. Review the retrieved context and memory from previous tasks.
    2. Generate a final answer following all SYSTEM PROMPT rules from the prompt template.
    3. Ensure proper citation format with markdown links.
    4. Provide the answer in the requested language: {language_instruction}
    5. Adapt complexity based on user type: {user_type}
    """,
    expected_output="A concise, well-structured legal answer in the requested language by following all SYSTEM PROMPT rules with proper citations.",
    output_file="final_answer.txt",
    agent=FinalAnswerAgent,
    context=[RetrievalTask, MemoryTask],
    async_execution=True,
    markdown=True,
)


tasks = [
    RetrievalTask,
    MemoryTask,
    FinalAnswerTask,
]

