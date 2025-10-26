# app/agent/agents.py

from crewai import Agent
from app.agent.llm import main_llm, tiny_llm
from app.chains.prompts import PROMPT

RetrievalAgent = Agent(
    role="Retrieval Specialist",
    goal="""Rewrite query for retrieval, classify retrieval strategy, and retrieve relevant legal documents.""",
    backstory="""You're an expert at selecting hybrid/dense/sparse/specific search.""",
    llm=tiny_llm,
    allow_delegation=False,
    verbose=True,
    max_iter=1
)

MemoryAgent = Agent(
    role="Memory Manager",
    goal="Detect whether to load session/personal memory and return relevant past info.",
    backstory="You intelligently decide whether memory is needed.",
    llm=tiny_llm,
    allow_delegation=False,
    verbose=True,
)

WebSearchAgent = Agent(
    role="Legal Web Search Analyst",
    goal="Search the web and identify relevant legal sources (especially lex.uz).",
    backstory="Expert in retrieving legal reference pages and determining their relevance.",
    llm=tiny_llm,
    verbose=True
)

WebExtractorAgent = Agent(
    role="Legal Web Content Extractor",
    goal="Extract, clean, and structure legal text from document URLs.",
    backstory="Expert in legal document parsing, article extraction, and clarity formatting.",
    llm=tiny_llm,
    verbose=True
)

SystemPromptBuilderAgent = Agent(
    role="System Prompt Builder",
    goal="Construct the system prompt using retrieved context, chat history, user type, and language instructions.",
    backstory="You create effective system prompts for LLMs.",
    llm=tiny_llm,
    allow_delegation=False,
    verbose=True,
)

FinalAnswerAgent = Agent(
    role="Legal Answer Composer",
    goal="Generate final correct, complete, legally structured answer by following all SYSTEM PROMPT rules.",
    backstory=f"""You are an advanced AI Legal Information Assistant and Advisor following these rules:

{PROMPT.template}

You provide professional answers based strictly on evidence from retrieved context.""",
    llm=main_llm,
    allow_delegation=False,
    verbose=True,
)

agents = [
    RetrievalAgent,
    MemoryAgent,
    WebSearchAgent,
    WebExtractorAgent,
    SystemPromptBuilderAgent,
    FinalAnswerAgent,
]
