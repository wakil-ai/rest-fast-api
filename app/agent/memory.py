# app/agent/memory.py

from crewai import Agent, Task, Crew, Process
from app.agent.tools import SessionMemoryTool, PersonalMemoryTool
from app.agent.llm import tiny_llm


# Agent Definition
MemoryAgent = Agent(
    role="Memory Router & Summarizer",
    goal=(
        "Retrieve session and personal memory for the user, then summarize the most relevant context "
        "to help answer their current question. ALWAYS use the tools to fetch memory data and ALWAYS "
        "provide meaningful summaries of what was retrieved."
    ),
    backstory=(
        "You are the Memory Manager for a legal assistant system. Your critical job is to:\n"
        "1. Use tools to fetch BOTH session and personal memory (don't skip calling tools)\n"
        "2. Read the returned memory data carefully\n"
        "3. Summarize only the RELEVANT facts that help answer the current question\n"
        "4. Never return empty notes if the tools returned actual data\n"
        "5. Focus on: previous questions asked, answers given, legal concepts discussed, dates, article numbers\n"
        "6. Keep summaries concise (max 100 words each) but informative\n"
        "You analyze each user question to determine which memory sources are relevant and provide "
        "concise, factual summaries without inventing or inferring beyond available data."
    ),
    llm=tiny_llm,
    allow_delegation=False,
    tools=[SessionMemoryTool(), PersonalMemoryTool()],
    verbose=True,
    max_iter=3,  # Allow more iterations to properly fetch and summarize
)


# Task Definition 
memory_task = Task(
    description="""
    Analyze the user's query and retrieve relevant session and personal memory notes.
    
    MANDATORY INSTRUCTIONS:
    1. First, read the user's QUERY carefully: {query}
    2. For SESSION MEMORY: Call get_session_memory tool with user_id={user_id} and session_id={session_id}
       - If tool returns conversations/context, extract and summarize the MOST RELEVANT points (max 100 words)
       - Focus on previous questions, answers, and decisions that help answer the current query
       - If no relevant session data, return empty string
    
    3. For PERSONAL MEMORY: Call get_personal_memory tool with user_id={user_id} and query={query}
       - If tool returns personal preferences/facts, summarize relevant points (max 100 words)
       - Focus on user preferences, domain expertise, or historical context
       - If no relevant personal data, return empty string
    
    4. CRITICAL: Do NOT return empty notes if tools returned data!
       - Always summarize what the tools returned
       - Be specific about previous answers, legal concepts discussed
       - Include article numbers, names, or key facts mentioned before
    
    INPUTS:
    - query: {query}  # The user's question
    - user_id: {user_id}  # ID of the user
    - session_id: {session_id}  # ID of the current chat session
    
    OUTPUT MUST BE VALID JSON with exactly these keys:
    {{
        "session_notes": "<concise summary of session context or empty string>",
        "personal_notes": "<concise summary of personal context or empty string>"
    }}
    """,
    expected_output="""
    {
    "session_notes": "Previous question about medical examination for marriage, answered with details about mandatory check requirements",
    "personal_notes": "User is asking legal questions, likely interested in family law topics"
    }
    """,
    agent=MemoryAgent,
    output_key="memory_json",
    verbose=True,
)


memory_crew = Crew(
    agents=[MemoryAgent],
    tasks=[memory_task],
    process=Process.sequential,
    verbose=True,
)