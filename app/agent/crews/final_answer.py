# app/agent/final_answer.py

from crewai import Agent, Task, Crew, Process
from app.agent.llm import main_llm  
from app.chains.prompts import SYSTEM_PROMPT


FinalAnswerAgent = Agent(
    role="Final Answer Composer",
    goal=SYSTEM_PROMPT + """
    
    Query: {query}
    """,
    backstory=(
        "You synthesize only the provided legal texts into a precise, user-appropriate legal answer. "
        "You never invent sources or facts beyond CONTEXT. You strictly obey citation formatting and "
        "language/script instructions."
    ),
    llm=main_llm,
    allow_delegation=False,
    verbose=True,
)


final_answer_task = Task(
    description="""
    Produce the final legal answer by strictly following the SYSTEM_PROMPT (already embedded in the agent instructions).

    Requirements:
    - Obey [STRICT CONTEXT ADHERENCE], [GREETING RULE], [SOURCE CITATION], [LANGUAGE RULES], [MEMORY USAGE], and
    the dynamic [ANSWER STRUCTURE] for {user_type}.
    - Answer ONLY using {context}. If none of the context is relevant, state that clearly in {language_instruction}.
    - Use {chat_history} and any provided memory notes ONLY if they help personalize without changing legal facts.
    - Write in {language_instruction}.
    - Output should be well-structured markdown, not JSON.

    INPUTS:
    - query: {query}
    - context: {context}                # concatenated legal snippets with any source metadata already attached
    - chat_history: {chat_history}      # prior turns if needed for personalization (do not overuse)
    - user_type: {user_type}            # "citizen" or "lawyer"
    - language_instruction: {language_instruction}  # e.g., "Uzbek Latin", "Russian", etc.

    Output:
    - A single, polished markdown answer that follows the SYSTEM_PROMPT, including properly formatted source citations
    (markdown links) and 2–3 follow-up questions in the user's language when applicable.
    """,
    expected_output="""
    <final markdown answer strictly following SYSTEM_PROMPT>

    # Notes:
    # - No JSON output needed
    """,
    agent=FinalAnswerAgent,
    output_key="final_answer_markdown",
    verbose=True,
)


final_answer_crew = Crew(
    agents=[FinalAnswerAgent],
    tasks=[final_answer_task],
    process=Process.sequential,
    verbose=True
)