# app/agent/retrieval.py
from crewai import Agent, Task, Crew, Process
from app.agent.llm import tiny_llm
from app.core.config import settings


RETRIEVAL_AGENT_INSTRUCTIONS = """
You are the Retrieval Specialist. Your job is to:
1) Optionally rewrite the QUERY for better retrieval (preserve meaning and language).
2) Choose one strategy: "hybrid", "dense", "sparse", or "specific".
   - hybrid: combine dense (semantic) + sparse (BM25) when you need both recall & precision.
   - dense: meaning-based similarity when synonyms/paraphrases matter.
   - sparse: keyword/BM25 when exact terms control relevance.
   - specific: ONLY when the QUERY explicitly names an article number or a unique statute identifier
     (e.g., “Article 123 of the Civil Code”, “ПКМ №43”, “ЗРУ-650”, “Fuqarolik kodeksining 115-moddasi”). Do NOT pick "specific" otherwise.
   
- Always output valid JSON with EXACTLY the keys: query_rewrite, strategy.

OUTPUT SCHEMA (must match exactly):
{
  "query_rewrite": "<string, possibly identical to QUERY if no rewrite needed>",
  "strategy": "hybrid" | "dense" | "sparse" | "specific",
}
"""

RetrievalAgent = Agent(
    role="Retrieval Specialist",
    goal=RETRIEVAL_AGENT_INSTRUCTIONS,
    backstory=(
        "You are expert at hybrid and targeted retrieval for legal queries. "
        "You only return faithful snippets with minimal noise, never invent sources, "
        "and you strictly follow the JSON schema."
    ),
    llm=tiny_llm,
    allow_delegation=False,
    verbose=settings.DEBUG,
    max_iter=3,
)

retrieval_task = Task(
    description="""
    Decide the retrieval strategy ("hybrid" | "dense" | "sparse" | "specific") and fetch top legal snippets with source URLs when available.

    Rules:
    - Use "specific" ONLY if the QUERY explicitly mentions an article number or a unique statute identifier
    (e.g., “Article 123 of the Civil Code”, “ПКМ №43”, “ЗРУ-650”). Otherwise, do NOT choose "specific".
    - Optionally rewrite the QUERY to improve retrieval while preserving meaning and language.
    
    INPUTS:
    - QUERY: {query}
    """,
    expected_output="""
    {
    "query_rewrite": "<string, possibly identical to QUERY if no rewrite needed>",
    "strategy": "hybrid",
    }
    """,
    agent=RetrievalAgent,
    output_key="retrieval_json",
    verbose=settings.DEBUG,
)

retrieval_crew = Crew(
    agents=[RetrievalAgent],
    tasks=[retrieval_task],
    process=Process.sequential,
    verbose=settings.DEBUG,
    tracing=settings.TRACING,
)
