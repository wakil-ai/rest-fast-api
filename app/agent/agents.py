# app/agent/agents.py
from crewai import Agent
from app.agent.llm import main_llm, tiny_llm
from app.agent.tools import (
    RetrievalTool,
    SessionMemoryTool,
    PersonalMemoryTool,
    WebSearchTool,
    WebExtractionTool,
)
from app.chains.prompts import PROMPT

# --- Retrieval Agent ---
RetrievalAgent = Agent(
    role="Retrieval Specialist",
    goal=(
        "Rewrite the user query (if needed), choose the best retrieval strategy "
        "(hybrid/dense/sparse/specific), and return the top relevant legal snippets "
        "with source URLs when available.\n\n"
        "Retrieval strategies:\n"
        "- hybrid: combine dense (semantic) + sparse (BM25) for balanced results.\n"
        "- dense: semantic similarity (meaning-based).\n"
        "- sparse: keyword relevance (BM25).\n"
        "- specific: targeted for explicit article numbers; only use when the query "
        "mentions an article number (e.g., 'Article 123 of the Civil Code...').\n\n"
        "If retrieved context is insufficient (empty, low confidence, or no clear legal basis), "
        "DELEGATE to web search agents."
    ),
    backstory="You’re an expert at selecting hybrid/dense/sparse/specific strategies and knowing when to switch to the web.",
    llm=tiny_llm,
    function_calling_llm=tiny_llm,     # cheaper model for tool calls
    allow_delegation=True,             # <-- delegation enabled
    verbose=True,
    tools=[
        RetrievalTool(result_as_answer=False),  # return structured tool output to the agent
        # delegation will hand off to WebSearch/WebExtractor agents (below)
    ],
    max_iter=1
)

# --- Memory Agent ---
MemoryAgent = Agent(
    role="Memory Manager",
    goal="Detect whether session or personal memory is relevant and return concise, relevant items only.",
    backstory="You load only what helps answer the current legal question.",
    llm=tiny_llm,
    allow_delegation=False,
    tools=[SessionMemoryTool(), PersonalMemoryTool()],
    verbose=True,
)

# --- Web Search Agent ---
WebSearchAgent = Agent(
    role="Legal Web Search Analyst",
    goal="Search the web and identify relevant legal sources (especially lex.uz). Return unique, non-duplicated URLs.",
    backstory="Expert at retrieving legal references and judging their relevance.",
    llm=tiny_llm,
    function_calling_llm=tiny_llm,
    tools=[WebSearchTool],
    verbose=True
)

# --- Web Extraction Agent ---
WebExtractorAgent = Agent(
    role="Legal Web Content Extractor",
    goal="Extract, clean, and structure legal text from provided URLs. Keep article numbers, headings, and URLs.",
    backstory="Expert in legal document parsing, article extraction, and clarity formatting.",
    llm=tiny_llm,
    function_calling_llm=tiny_llm,
    tools=[WebExtractionTool],
    verbose=True
)

# --- System Prompt Builder Agent ---
SystemPromptBuilderAgent = Agent(
    role="System Prompt Builder",
    goal="Construct the final system prompt from: retrieved context, web-extracted context, relevant memories, chat history, user_type, and language_instruction.",
    backstory="You create effective system prompts for LLMs by strictly following the policy template.",
    llm=tiny_llm,
    allow_delegation=False,
    verbose=True,
)

# --- Final Answer Agent ---
FinalAnswerAgent = Agent(
    role="Legal Answer Composer",
    goal="Generate a correct, complete, legally structured answer by following all SYSTEM PROMPT rules.",
    backstory=(
        "You are an advanced AI Legal Information Assistant and Advisor following these rules:\n"
        f"{PROMPT.template}\n"
        "You provide professional answers strictly from the provided context."
    ),
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
