# app/agent/memory.py

from crewai import Agent, Task
from app.agent.tools import SessionMemoryTool, PersonalMemoryTool
from app.agent.llm import tiny_llm


# Agent Definition
MemoryAgent = Agent(
    role="Memory Router & Summarizer",
    goal=(
        "Decide whether session memory, personal memory, both, or neither are "
        "needed for the user's question, and return only concise, relevant notes."
    ),
    backstory=(
        "You are the Memory Manager for a legal assistant system. "
        "You analyze each user question to determine which memory sources are relevant. "
        "If a memory type is needed, you call the corresponding tool to load it "
        "and summarize only the essential, factual details directly related to the question. "
        "You never invent or infer beyond available memory data. "
        "Each output must stay concise (≤120 words per field) and factual."
    ),
    llm=tiny_llm,
    allow_delegation=False,
    tools=[SessionMemoryTool(), PersonalMemoryTool()],
    verbose=True,
)


# Task Definition 
memory_task = Task(
    description="""
    Given a user {query}, decide whether session memory, personal memory, both, or neither are needed to answer it.

    If a memory type is needed, use the corresponding tool to load it and summarize only the minimal, relevant facts 
    necessary to answer the question. Keep each notes field concise (≤120 words), factual, and non-duplicative.
    If a memory type is not needed, return an empty string for that field.

    Do not invent information. Output must be valid JSON with exactly the following keys.

    INPUTS:
    - query: {query}  # The user's question
    - user_id: {user_id}  # ID of the user
    - session_id: {session_id}  # ID of the current chat session
    """,
    expected_output="""
    {
    "session_notes": "<concise, relevant notes from session memory, or empty string if not needed>",
    "personal_notes": "<concise, relevant notes from personal memory, or empty string if not needed>"
    }
    """,
    agent=MemoryAgent,
    output_key="memory_json",
    verbose=True,
)
