# app/agent/tasks.py
from crewai import Task
from typing import List
from app.agent.agents import (
    RetrievalAgent,
    MemoryAgent,
    WebSearchAgent,
    WebExtractorAgent,
    SystemPromptBuilderAgent,
    FinalAnswerAgent,
)
from app.chains.prompts import PROMPT

# -------------------------
# Task 1: Relevant Memory
# -------------------------
memory_task = Task(
    description="""Given the user QUESTION, fetch only the most relevant session and personal memories.
Return concise notes suitable for inclusion in a system prompt.

INPUTS:
- QUESTION: {{ question }}

OUTPUT JSON KEYS:
- session_notes: str
- personal_notes: str
- merged_memory: str""",
    expected_output="""{
  "session_notes": "",
  "personal_notes": "",
  "merged_memory": ""
}""",
    agent=MemoryAgent,
    output_key="memory_json",
    verbose=True,
)

# ----------------------------------------------
# Task 2: Retrieval (hybrid/dense/sparse/specific)
# ----------------------------------------------
retrieval_task = Task(
    description="""Decide retrieval strategy (hybrid/dense/sparse/specific) and fetch top legal snippets
with source URLs (if available). If no article number is present, do NOT choose 'specific'.
If the retrieved context is insufficient (empty, low confidence, or not enough to answer the QUESTION),
set need_web=true to trigger web fallback.

INPUTS:
- QUESTION: {{ question }}

OUTPUT JSON KEYS:
- query_rewrite: str
- strategy: "hybrid" | "dense" | "sparse" | "specific"
- docs: list of {text: str, source: str|null, score: float|null}
- need_web: bool""",
    expected_output="""{
  "query_rewrite": "",
  "strategy": "hybrid",
  "docs": [
    {"text": "", "source": "", "score": 0.0}
  ],
  "need_web": false
}""",
    agent=RetrievalAgent,
    output_key="retrieval_json",
    verbose=True,
)

# ---------------------------------
# Task 3: Web Search (conditional)
# ---------------------------------
web_search_task = Task(
    description="""ONLY execute if the previous Retrieval output indicates need_web=true.
You will receive the Retrieval JSON in your context. If 'need_web' is false,
return an empty JSON object: {}.

Otherwise, search the web for authoritative legal sources (prefer lex.uz).
Return 3–6 UNIQUE, non-duplicated URLs that directly address the QUESTION.

INPUTS:
- QUESTION: {{ question }}

OUTPUT JSON KEYS:
- urls: list[str]""",
    expected_output="""{"urls": ["https://lex.uz/docs/...", "..."]}""",
    agent=WebSearchAgent,
    context=[retrieval_task],
    output_key="web_json",
    verbose=True,
)

# -----------------------------------
# Task 4: Web Extraction (conditional)
# -----------------------------------
web_extract_task = Task(
    description="""If the previous Web Search returned URLs in its JSON (provided in your context),
extract legal text from each URL (keep article numbers/headings and URLs).
Normalize the result to the same doc shape used by retrieval: {text, source, score?}.

If there are no URLs (or an empty list), return: {"docs": []}.

OUTPUT JSON KEYS:
- docs: list of {text: str, source: str, score: float|null}""",
    expected_output="""{"docs": [{"text": "", "source": "https://...", "score": null}]}""",
    agent=WebExtractorAgent,
    context=[web_search_task],
    output_key="web_docs_json",
    verbose=True,
)

# ------------------------------
# Task 5: System Prompt Builder
# ------------------------------
prompt_builder_task = Task(
    description=(
        """Build the FINAL SYSTEM PROMPT using either the provided system_policy (if any)
or the fallback policy template below. You will receive the Retrieval JSON,
Web Extraction JSON, and Memory JSON in your context.

FALLBACK POLICY TEMPLATE:
"""
        + PROMPT.template
        + """
INPUTS (from kickoff):
- policy_override: {{ system_policy }}  (may be empty)
- user_type: {{ user_type }}
- language_instruction: {{ language_instruction }}
- chat_history: {{ chat_history }}

REQUIREMENTS:
- Build a CONTEXT block concatenating all normalized docs (retrieval + web) with their sources.
- Include chat_history and merged_memory as separate sections.
- Enforce the citation casing and markdown-link style rules.
- Return a single string field `system_prompt` ready to be used as a system message."""
    ),
    expected_output="""{"system_prompt": "<FULL SYSTEM PROMPT STRING>"}""",
    agent=SystemPromptBuilderAgent,
    context=[retrieval_task, web_extract_task, memory_task],
    output_key="system_prompt_json",
    verbose=True,
)

# ------------------------------
# Task 6: Final Answer Composer
# ------------------------------
final_answer_task = Task(
    description="""Use the SYSTEM PROMPT from the previous task as your system message and answer the user QUESTION.
Follow ALL policy rules (language, structure by user_type, strict context adherence, citation style, greeting rule, etc.).

INPUTS:
- QUESTION: {{ question }}

OUTPUT:
- The final answer text only (no debug, no prefaces).""",
    expected_output="""The final legally-structured answer in the requested language/script.""",
    agent=FinalAnswerAgent,
    context=[prompt_builder_task],
    output_key="final_answer",
    verbose=True,
)

# Export tasks in order for Process.sequential
tasks: List[Task] = [
    memory_task,
    retrieval_task,
    web_search_task,
    web_extract_task,
    prompt_builder_task,
    final_answer_task,
]
