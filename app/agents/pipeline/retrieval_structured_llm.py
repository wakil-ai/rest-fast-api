"""
Structured LLM + Tavily for LangGraph retrieval nodes.

Memory summarization, retrieval strategy, and context evaluation use
OpenAI ``response_format=json_object``; web augmentation uses the Tavily client
when ``TAVILY_API_KEY`` is set.
"""

from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path
from typing import Any

from openai import AsyncOpenAI
from tavily import TavilyClient

from app.agents.pipeline.schemas import (
    ContextEvaluationResponse,
    MemoryAgentResponse,
    RetrievalStrategyResponse,
    WebSearchDocument,
    WebSearchResponse,
)
from app.core.config import settings
from app.core.logger import logger
from app.utils.tokens import count_tokens, truncate_to_token_limit


def _agents_config_path() -> Path:
    return Path(__file__).resolve().parent / "config" / "agents.yaml"


@lru_cache
def _agents_yaml() -> dict[str, Any]:
    path = _agents_config_path()
    if not path.exists():
        return {"agents": {}}
    import yaml

    with path.open(encoding="utf-8") as f:
        data = yaml.safe_load(f) or {}
    return data.get("agents", {})


def _agent_instructions(name: str) -> str:
    agents = _agents_yaml()
    block = agents.get(name) or {}
    role = block.get("role", "")
    goal = block.get("goal", "")
    back = block.get("backstory", "")
    return f"Role: {role}\n\nInstructions:\n{goal}\n\nBackground:\n{back}".strip()


def retrieval_chain_model() -> str:
    return settings.RETRIEVAL_CHAIN_MODEL or "gpt-4.1-mini"


def retrieval_chain_max_tokens() -> int:
    raw = settings.RETRIEVAL_CHAIN_MAX_TOKENS
    try:
        return max(128, min(int(raw), 4096))
    except (TypeError, ValueError):
        return 1024


def _token_param(model: str) -> str:
    if "gpt-5.2" in model or model.startswith("o1"):
        return "max_completion_tokens"
    return "max_tokens"


async def _openai_json_object(system: str, user: str) -> dict[str, Any]:
    if not settings.OPENAI_API_KEY:
        logger.warning("[retrieval_chain] OPENAI_API_KEY unset; skipping JSON LLM call")
        return {}
    model = retrieval_chain_model()
    client = AsyncOpenAI(api_key=settings.OPENAI_API_KEY)
    tparam = _token_param(model)
    kwargs: dict[str, Any] = {
        "model": model,
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
        "temperature": 0.0,
        "response_format": {"type": "json_object"},
        tparam: retrieval_chain_max_tokens(),
    }
    try:
        resp = await client.chat.completions.create(**kwargs)
    except Exception as e:
        logger.warning(f"[retrieval_chain] OpenAI JSON call failed: {e}", exc_info=True)
        return {}
    raw = (resp.choices[0].message.content or "").strip()
    if not raw:
        return {}
    try:
        return json.loads(raw.replace(": None", ": null"))
    except json.JSONDecodeError:
        logger.warning(f"[retrieval_chain] Invalid JSON from model: {raw[:300]}...")
        return {}


async def summarize_memory_llm(
    query: str, session_memory_json: str, personal_memory_json: str
) -> MemoryAgentResponse:
    spec = _agent_instructions("memory_summarizer")
    system = (
        f"{spec}\n\nYou must respond with a single JSON object only, with keys exactly: "
        '"session_notes" (string), "personal_notes" (string), "resolved_query" (string). '
        "No markdown fences."
    )
    user = (
        f"query:\n{query}\n\nsession_memory (JSON):\n{session_memory_json}\n\n"
        f"personal_memory (JSON):\n{personal_memory_json}"
    )
    data = await _openai_json_object(system, user)
    if not data:
        return MemoryAgentResponse(
            session_notes="",
            personal_notes="",
            resolved_query=query,
        )
    return MemoryAgentResponse(
        session_notes=str(data.get("session_notes") or ""),
        personal_notes=str(data.get("personal_notes") or ""),
        resolved_query=str(data.get("resolved_query") or query),
    )


_RETRIEVAL_STRATEGY_FILE_CONTEXT_MAX_TOKENS = 8000


async def retrieval_strategy_llm(
    query: str, *, file_context: str = ""
) -> RetrievalStrategyResponse:
    spec = _agent_instructions("retrieval_specialist")
    system = (
        f"{spec}\n\nRespond with one JSON object only. Required keys: "
        '"query_rewrite" (string, one refined search query; keep the user\'s language unless '
        "a short clarifying phrase in another language clearly helps retrieval), "
        '"strategy" (one of hybrid, dense, sparse), '
        '"assistant" (either soliq or umumiy), "reasoning" (string). No markdown.'
    )
    user_parts: list[str] = [f"QUERY:\n{query}"]
    fc = (file_context or "").strip()
    if fc:
        if count_tokens(fc) > _RETRIEVAL_STRATEGY_FILE_CONTEXT_MAX_TOKENS:
            fc = truncate_to_token_limit(
                fc, _RETRIEVAL_STRATEGY_FILE_CONTEXT_MAX_TOKENS
            )
        user_parts.append(
            "UPLOADED_DOCUMENTS (user attached; when QUERY is short or only an instruction "
            'such as "tahlil qil" / "analyze", derive concrete legal topics, parties, '
            "article references, and domain terms from this text for query_rewrite):\n"
            f"{fc}"
        )
    user = "\n\n".join(user_parts)
    data = await _openai_json_object(system, user)
    if not data:
        return RetrievalStrategyResponse(
            strategy="hybrid",
            query_rewrite=query,
            assistant="umumiy",
            reasoning="",
        )
    strat = str(data.get("strategy") or "hybrid").lower()
    if strat == "specific":
        strat = "hybrid"
    if strat not in ("hybrid", "dense", "sparse"):
        strat = "hybrid"
    return RetrievalStrategyResponse(
        strategy=strat,
        query_rewrite=str(data.get("query_rewrite") or query),
        assistant=str(data.get("assistant") or "umumiy"),
        reasoning=data.get("reasoning"),
    )


_MAX_EVAL_CONTEXT = int(settings.RETRIEVAL_CONTEXT_TOKEN_LIMIT)


async def context_evaluation_llm(query: str, context: str) -> ContextEvaluationResponse:
    spec = _agent_instructions("context_evaluator")
    ctx = context if len(context) <= _MAX_EVAL_CONTEXT else context[:_MAX_EVAL_CONTEXT]
    system = (
        f"{spec}\n\nRespond with one JSON object only. Keys: "
        '"is_sufficient" (boolean), "reasoning" (string), "missing_info" (string, may be empty). '
        "No markdown."
    )
    user = f"QUERY:\n{query}\n\nRETRIEVED_CONTEXT:\n{ctx}"
    data = await _openai_json_object(system, user)
    if not data:
        return ContextEvaluationResponse(
            is_sufficient=True,
            reasoning="Evaluator unavailable; proceeding with corpus context.",
            missing_info="",
        )
    return ContextEvaluationResponse(
        is_sufficient=bool(data.get("is_sufficient", True)),
        reasoning=str(data.get("reasoning") or ""),
        missing_info=str(data.get("missing_info") or ""),
    )


async def web_search_tavily(query: str) -> WebSearchResponse:
    if not settings.TAVILY_API_KEY:
        logger.info("[retrieval_chain] TAVILY_API_KEY unset; skipping web search")
        return WebSearchResponse(docs=[])

    try:
        client = TavilyClient(api_key=settings.TAVILY_API_KEY)
        raw = client.search(
            query,
            max_results=5,
            search_depth="advanced",
            include_answer=True,
        )
    except Exception as e:
        logger.warning(f"[retrieval_chain] Tavily search failed: {e}", exc_info=True)
        return WebSearchResponse(docs=[])

    if isinstance(raw, dict):
        results = raw.get("results")
    else:
        results = getattr(raw, "results", None)
    if not isinstance(results, list):
        return WebSearchResponse(docs=[])

    docs: list[WebSearchDocument] = []
    for item in results[:5]:
        if not isinstance(item, dict):
            continue
        url = str(item.get("url") or "").strip()
        if not url:
            continue
        title = item.get("title")
        content = str(
            item.get("content") or item.get("snippet") or item.get("raw_content") or ""
        ).strip()
        docs.append(
            WebSearchDocument(
                url=url,
                title=str(title) if title else None,
                content=content or "(no extract returned)",
            )
        )
    return WebSearchResponse(docs=docs)
