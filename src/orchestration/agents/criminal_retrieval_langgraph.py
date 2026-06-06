"""
LangGraph criminal-case retrieval: LLM metadata filters -> Neo4j vector search with filter;
optional parallel hybrid (vector + full-text index) when configured; RRF fusion; Mongo excerpts.

LangChain Neo4jVector does not support metadata filter + hybrid in one query, so we run both
when a full-text index is configured and merge by reciprocal rank fusion on ``doc_id``.

Fused context uses the top ``CRIMINAL_RETRIEVAL_TOP_CASES`` similar matters (default 3). No
arbitrary character caps on Mongo or section text — full documents are passed through.
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
from collections import defaultdict
from typing import Any, TypedDict

from langchain_community.vectorstores.neo4j_vector import Neo4jVector, SearchType
from langchain_core.documents import Document
from langchain_core.messages import HumanMessage, SystemMessage
from langgraph.graph import END, START, StateGraph
from pymongo import MongoClient

from src.core.config import settings
from src.core.dependencies import get_orchestration_service, get_prompt_registry
from src.orchestration.text import message_content_to_plain_str
from src.retrieval.embedding_manager import SiliconFlowLangChainEmbeddings

logger = logging.getLogger(__name__)

CRIMINAL_RETRIEVAL_TOP_CASES = 3

_SUPPORTED_METADATA_OPERATORS = frozenset(
    {
        "$eq",
        "$ne",
        "$lt",
        "$lte",
        "$gt",
        "$gte",
        "$in",
        "$nin",
        "$between",
        "$like",
        "$ilike",
    }
)
_ALLOWED_METADATA_FIELDS = frozenset(
    {
        "db_name",
        "claim_document_type",
        "instance",
        "instance_type",
        "judge",
    }
)


class CriminalRetrievalState(TypedDict, total=False):
    question: str
    filters_raw: str
    filters: dict[str, Any]
    docs_vector_filtered: list[Document]
    docs_hybrid: list[Document]
    fused_documents: list[Document]
    context_markdown: str


def _doc_id(doc: Document) -> str | None:
    meta = doc.metadata or {}
    for key in ("doc_id", "docId", "claim_id"):
        val = meta.get(key)
        if val:
            return str(val)
    return None


def _ordered_doc_ids(docs: list[Document]) -> list[str]:
    out: list[str] = []
    for d in docs:
        did = _doc_id(d)
        if did and did not in out:
            out.append(did)
    return out


def reciprocal_rank_fusion(rank_lists: list[list[str]], *, rrf_k: int = 60) -> dict[str, float]:
    scores: dict[str, float] = defaultdict(float)
    for ids in rank_lists:
        for rank, doc_id in enumerate(ids, start=1):
            scores[doc_id] += 1.0 / (rrf_k + rank)
    return dict(scores)


def _neo4j_common_kwargs() -> dict[str, Any]:
    return {
        "url": settings.NEO4J_URI,
        "username": settings.NEO4J_USERNAME,
        "password": settings.NEO4J_PASSWORD,
        "database": settings.NEO4J_DATABASE,
    }


def _close_store(store: Neo4jVector) -> None:
    driver = getattr(store, "_driver", None)
    if driver is not None:
        try:
            driver.close()
        except Exception:
            logger.debug("Neo4j driver close failed", exc_info=True)


def _vector_only_store() -> Neo4jVector:
    return Neo4jVector.from_existing_index(
        SiliconFlowLangChainEmbeddings(),
        index_name=settings.NEO4J_CASE_VECTOR_INDEX,
        search_type=SearchType.VECTOR,
        retrieval_query=settings.NEO4J_CASE_VECTOR_RETRIEVAL_QUERY,
        **_neo4j_common_kwargs(),
    )


def _hybrid_store() -> Neo4jVector | None:
    fts = (settings.NEO4J_CASE_FULLTEXT_INDEX or "").strip()
    if not fts:
        return None
    return Neo4jVector.from_existing_index(
        SiliconFlowLangChainEmbeddings(),
        index_name=settings.NEO4J_CASE_VECTOR_INDEX,
        search_type=SearchType.HYBRID,
        keyword_index_name=fts,
        retrieval_query=settings.NEO4J_CASE_VECTOR_RETRIEVAL_QUERY,
        **_neo4j_common_kwargs(),
    )


async def node_plan_filters(state: CriminalRetrievalState) -> dict[str, Any]:
    question = (state.get("question") or "").strip()
    if not question:
        return {"filters_raw": "", "filters": {}}

    llm = get_orchestration_service().lite_llm
    registry = get_prompt_registry()
    system = registry.get_prompt("cypher_agent").format()
    from src.core.langfuse_tracing import LlmRunName, traced_ainvoke

    response = await traced_ainvoke(
        llm,
        [SystemMessage(content=system), HumanMessage(content=question)],
        run_name=LlmRunName.CYPHER_FILTER_PLANNING,
    )
    raw = message_content_to_plain_str(getattr(response, "content", response))
    filters = parse_metadata_filter_json(raw)
    logger.info(f"Criminal retrieval metadata filters: {filters}")
    return {"filters_raw": raw, "filters": filters}


async def node_retrieve_vector_filtered(state: CriminalRetrievalState) -> dict[str, Any]:
    question = state.get("question") or ""
    filters = state.get("filters") or {}

    def _run() -> list[Document]:
        store = _vector_only_store()
        try:
            try:
                return store.similarity_search(
                    question,
                    k=CRIMINAL_RETRIEVAL_TOP_CASES,
                    filter=filters or None,
                )
            except Exception as error:
                if not filters:
                    raise
                logger.warning(
                    "Criminal retrieval filtered search failed; retrying without "
                    f"metadata filter. filters={filters!r} error={error}",
                    exc_info=True,
                )
                return store.similarity_search(
                    question, k=CRIMINAL_RETRIEVAL_TOP_CASES, filter=None
                )
        finally:
            _close_store(store)

    docs = await asyncio.to_thread(_run)
    return {"docs_vector_filtered": docs}


async def node_retrieve_hybrid(state: CriminalRetrievalState) -> dict[str, Any]:
    question = state.get("question") or ""
    filters = state.get("filters") or {}
    if filters:
        # LangChain Neo4jVector: metadata filter cannot be combined with hybrid search.
        return {"docs_hybrid": []}

    def _run() -> list[Document]:
        store = _hybrid_store()
        if store is None:
            return []
        try:
            return store.similarity_search(question, k=CRIMINAL_RETRIEVAL_TOP_CASES)
        finally:
            _close_store(store)

    docs = await asyncio.to_thread(_run)
    return {"docs_hybrid": docs}


async def node_retrieve_both(state: CriminalRetrievalState) -> dict[str, Any]:
    """Run filtered vector search and optional hybrid search (sequential, same event loop)."""
    vf, hy = await asyncio.gather(
        node_retrieve_vector_filtered(state),
        node_retrieve_hybrid(state),
    )
    return {**vf, **hy}


async def node_fuse_documents(state: CriminalRetrievalState) -> dict[str, Any]:
    filtered = state.get("docs_vector_filtered") or []
    hybrid = state.get("docs_hybrid") or []

    if not hybrid:
        return {"fused_documents": filtered[:CRIMINAL_RETRIEVAL_TOP_CASES]}

    fusion = reciprocal_rank_fusion(
        [_ordered_doc_ids(filtered), _ordered_doc_ids(hybrid)]
    )
    by_id: dict[str, Document] = {}
    for doc in filtered + hybrid:
        did = _doc_id(doc)
        if did and did not in by_id:
            by_id[did] = doc

    ranked_ids = sorted(fusion.keys(), key=lambda x: fusion[x], reverse=True)
    fused = [by_id[i] for i in ranked_ids if i in by_id][:CRIMINAL_RETRIEVAL_TOP_CASES]
    if not fused:
        fused = filtered[:CRIMINAL_RETRIEVAL_TOP_CASES] or hybrid[:CRIMINAL_RETRIEVAL_TOP_CASES]
    return {"fused_documents": fused}


def _mongo_client() -> MongoClient | None:
    if not settings.MONGODB_URI:
        return None
    return MongoClient(settings.MONGODB_URI, serverSelectionTimeoutMS=3000)

def _format_case_block_sample(
    rank: int,
    *,
    case: dict[str, Any] | None,
    sections: list[dict[str, Any]],
    neo4j_snippet: str,
) -> str:
    """Formats a single case block for LLM/RAG consumption."""

    def _list(value: Any) -> str:
        return ", ".join(map(str, value or []))

    def _field(key: str, default: str = "") -> str:
        return str(case.get(key, default)) if case else default

    lines = [f"### Case {rank}", ""]

    metadata = {
        "Raqami": _field("case_number"),
        "Xulosasi": _field("case_summary", neo4j_snippet),
        "Ayblangan Moddalar": _list(case.get("article_numbers")) if case else "",
        "Ayblovlar": _list(case.get("claim_articles")) if case else "",
        "Case Hujjat Turi": _list(case.get("claim_document_types")) if case else "",
        "Sud zali": _field("db_name"),
        "Sud bo'lib o'tgan sana": _field("hearing_date"),
        "Instansiya Turi": _field("instance_type"),
        "Sudya": _field("judge"),
    }

    lines.extend(f"{k}: {v}" for k, v in metadata.items())

    body = "\n\n".join(
        (sec.get("text") or "").strip()
        for sec in sections
        if (sec.get("text") or "").strip()
    ).strip()

    lines.extend(
        [
            "",
            "#### TEXT:",
            "",
            body or neo4j_snippet.strip(),
        ]
    )

    return "\n".join(lines)


def _build_context_sync(documents: list[Document]) -> str:
    client = _mongo_client()
    parts: list[str] = []
    try:
        for rank, doc in enumerate(documents, start=1):
            did = _doc_id(doc)
            neo4j_snippet = (doc.page_content or "").strip()

            case: dict[str, Any] | None = None
            sections: list[dict[str, Any]] = []
            if client and did:
                db = client[settings.CRIMINAL_CASES_MONGODB_DATABASE]
                found = db.cases.find_one({"doc_id": did})
                if isinstance(found, dict):
                    case = found
                sections = list(
                    db.case_sections.find(
                        {"doc_id": did}, {"_id": 0, "text": 1, "title": 1, "section_id": 1}
                    ).sort("order", 1)
                )

            parts.append(
                _format_case_block_sample(
                    rank,
                    case=case,
                    sections=sections,
                    neo4j_snippet=neo4j_snippet,
                )
            )
    finally:
        if client:
            client.close()

    return "\n\n".join(parts).strip() or "No criminal case documents were retrieved."


async def node_format_context(state: CriminalRetrievalState) -> dict[str, Any]:
    fused = state.get("fused_documents") or []
    text = await asyncio.to_thread(_build_context_sync, fused)
    return {"context_markdown": text}


def build_criminal_retrieval_graph():
    graph = StateGraph(CriminalRetrievalState)
    graph.add_node("plan_filters", node_plan_filters)
    graph.add_node("retrieve_both", node_retrieve_both)
    graph.add_node("fuse_documents", node_fuse_documents)
    graph.add_node("format_context", node_format_context)

    graph.add_edge(START, "plan_filters")
    graph.add_edge("plan_filters", "retrieve_both")
    graph.add_edge("retrieve_both", "fuse_documents")
    graph.add_edge("fuse_documents", "format_context")
    graph.add_edge("format_context", END)
    return graph.compile()


def _disabled_message() -> str:
    return (
        "Criminal case graph retrieval is disabled (`CRIMINAL_GRAPH_RETRIEVAL_ENABLED=false`) "
        "or Neo4j is not configured. Enable retrieval and set `NEO4J_URI` / `NEO4J_PASSWORD` "
        "to search indexed cases."
    )


async def arun_criminal_retrieval(question: str) -> str:
    if not settings.CRIMINAL_GRAPH_RETRIEVAL_ENABLED:
        return _disabled_message()
    if not settings.NEO4J_URI or not settings.NEO4J_PASSWORD:
        return _disabled_message()

    app = build_criminal_retrieval_graph()
    result = await app.ainvoke({"question": question})
    return str(result.get("context_markdown") or "")


def _strip_markdown_fence(text: str) -> str:
    text = text.strip()
    m = re.match(r"^```(?:json)?\s*([\s\S]*?)\s*```$", text)
    if m:
        return m.group(1).strip()
    m = re.search(r"```(?:json)?\s*([\s\S]*?)\s*```", text)
    if m:
        return m.group(1).strip()
    return text


def _first_balanced_object(text: str) -> str | None:
    """Return substring of the first top-level `{...}` with string-aware brace matching."""
    start = text.find("{")
    if start == -1:
        return None
    depth = 0
    in_string = False
    escape = False
    for i in range(start, len(text)):
        c = text[i]
        if in_string:
            if escape:
                escape = False
            elif c == "\\":
                escape = True
            elif c == '"':
                in_string = False
            continue
        if c == '"':
            in_string = True
            continue
        if c == "{":
            depth += 1
        elif c == "}":
            depth -= 1
            if depth == 0:
                return text[start : i + 1]
    return None


def _is_unknown_metadata_value(value: Any) -> bool:
    if isinstance(value, str):
        return value.strip().lower() == "unknown"
    if isinstance(value, list):
        return not value or all(_is_unknown_metadata_value(item) for item in value)
    return False


def _normalize_field_operator_value(value: Any) -> Any | None:
    """Normalize one field's filter value; return None to drop the field."""
    if _is_unknown_metadata_value(value):
        return None
    if not isinstance(value, dict):
        return value

    operator_keys = [k for k in value if isinstance(k, str) and k.startswith("$")]
    if not operator_keys:
        return value

    if len(value) == 1:
        operator, operand = next(iter(value.items()))
        if operator not in _SUPPORTED_METADATA_OPERATORS:
            return None
        if _is_unknown_metadata_value(operand):
            return None
        if operator in {"$in", "$nin"} and isinstance(operand, list):
            cleaned = [item for item in operand if not _is_unknown_metadata_value(item)]
            if not cleaned:
                return None
            if cleaned != operand:
                return {operator: cleaned}
        return value

    range_ops = {k: v for k, v in value.items() if k.startswith("$")}
    low = range_ops.get("$gte", range_ops.get("$gt"))
    high = range_ops.get("$lte", range_ops.get("$lt"))
    if low is not None and high is not None:
        return {"$between": [low, high]}

    logger.warning(
        "Dropping metadata field with unsupported operator combination: %s",
        value,
    )
    return None


def sanitize_metadata_filter(filters: dict[str, Any]) -> dict[str, Any]:
    """Make LLM filters LangChain-safe: drop unknowns, fix ranges, ignore bad fields."""
    if not filters:
        return {}

    if len(filters) == 1:
        key, value = next(iter(filters.items()))
        if key in ("$and", "$or") and isinstance(value, list):
            cleaned_children = [
                child
                for child in (sanitize_metadata_filter(item) for item in value)
                if child
            ]
            if not cleaned_children:
                return {}
            if len(cleaned_children) == 1:
                return cleaned_children[0]
            return {key: cleaned_children}

    _DISALLOWED_FIELDS = frozenset({"hearing_year", "hearing_date"})

    sanitized: dict[str, Any] = {}
    for field, value in filters.items():
        if field.startswith("$"):
            logger.warning("Ignoring unsupported top-level metadata operator: %s", field)
            continue
        if field in _DISALLOWED_FIELDS:
            logger.warning("Ignoring disallowed date/time metadata field: %s", field)
            continue
        if field not in _ALLOWED_METADATA_FIELDS:
            logger.warning("Ignoring unknown metadata field: %s", field)
            continue
        normalized = _normalize_field_operator_value(value)
        if normalized is not None:
            sanitized[field] = normalized
    return sanitized


def parse_metadata_filter_json(raw: str) -> dict:
    """Parse LLM output into a metadata dict; tolerate fences, chatter, and empty replies."""
    trimmed = _strip_markdown_fence(raw)
    if not trimmed:
        return {}
    for candidate in (trimmed, _first_balanced_object(trimmed) or ""):
        if not candidate:
            continue
        try:
            obj = json.loads(candidate)
            if isinstance(obj, dict):
                return sanitize_metadata_filter(obj)
        except json.JSONDecodeError:
            continue
    logger.warning(f"Could not parse metadata filter JSON from LLM; using empty filter. Raw (truncated): {raw[:500]}" if raw else "")
    return {}
