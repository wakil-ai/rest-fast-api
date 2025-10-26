# app/agent/agents.py

from crewai import Agent

# Coordinator decides which agents must act
CoordinatorAgent = Agent(
    role="Coordinator",
    goal="Analyze the query and determine which retrieval and memory steps are needed.",
    backstory="You manage strategy decisions.",
    llm="gpt-4o-mini",
    allow_delegation=True,
    verbose=True,
)

RetrievalAgent = Agent(
    role="Retrieval Specialist",
    goal="Rewrite query for retrieval, classify retrieval strategy, and retrieve top documents.",
    backstory="You're an expert at selecting hybrid/dense/sparse/specific search.",
    llm="gpt-4o-mini",
    allow_delegation=False,
    verbose=True,
)

MemoryAgent = Agent(
    role="Memory Manager",
    goal="Detect whether to load session/personal memory and return relevant past info.",
    backstory="You intelligently decide whether memory is needed.",
    llm="gpt-4o-mini",
    allow_delegation=False,
    verbose=True,
)

WebSearchAgent = Agent(
    role="Legal Web Search Analyst",
    goal="Search the web and identify relevant legal sources (especially lex.uz).",
    backstory="Expert in retrieving legal reference pages and determining their relevance.",
    llm="gpt-4o-mini",
    verbose=True
)

WebExtractorAgent = Agent(
    role="Legal Web Content Extractor",
    goal="Extract, clean, and structure legal text from document URLs.",
    backstory="Expert in legal document parsing, article extraction, and clarity formatting.",
    llm="gpt-4o-mini",
    verbose=True
)

FinalAnswerAgent = Agent(
    role="Legal Answer Composer",
    goal="Generate final correct, complete, legally structured answer.",
    backstory="You provide professional answers based strictly on evidence.",
    llm="gpt-4o",  # This maps to your Novita final answer call
    allow_delegation=False,
    verbose=True,
)
