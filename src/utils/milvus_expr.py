"""Milvus filter expressions and VARCHAR field limits."""

from __future__ import annotations

import json
import re
from typing import Any

from src.core.logger import logger

# Milvus schema max_length for ``text`` and ``hierarchy_path`` VARCHAR fields.
MILVUS_VARCHAR_TEXT_MAX = 65535


def normalize_milvus_expr(expr: str | None) -> str | None:
    """Return a Milvus filter string or None when no filter should be applied."""
    if expr is None:
        return None
    text = str(expr).strip()
    if not text:
        return None
    return text


def normalize_milvus_filter_llm_output(response: str) -> str:
    """
    Light cleanup of LLM filter output (fences, prefix, JSON wrapper).

    Does not reject expressions; invalid filters are handled at search time by
    falling back to unfiltered dense search.
    """
    text = (response or "").strip()
    if not text:
        return ""

    fence = re.fullmatch(r"```(?:\w+)?\s*(.*?)\s*```", text, flags=re.S)
    if fence:
        text = fence.group(1).strip()

    text = re.sub(r"^(filter|expr|expression)\s*:\s*", "", text, flags=re.I)
    text = text.strip().strip("`").strip()
    if not text:
        return ""

    extracted = _extract_expr_from_json_wrapper(text)
    if extracted is not None:
        return extracted

    return text


# Backward-compatible alias
sanitize_milvus_filter_llm_output = normalize_milvus_filter_llm_output


def _extract_expr_from_json_wrapper(text: str) -> str | None:
    """If the model returned JSON, pull out a nested filter/expr string when present."""
    if not (text.startswith("{") or text.startswith("[")):
        return None
    try:
        parsed: Any = json.loads(text)
    except json.JSONDecodeError:
        return None

    if isinstance(parsed, dict):
        for key in ("filter", "expr", "expression", "milvus_filter"):
            value = parsed.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip()
        return None

    return None


def truncate_milvus_varchar(text: str, *, field: str = "text") -> str:
    """Truncate text to Milvus VARCHAR max length (logs when truncated)."""
    if len(text) <= MILVUS_VARCHAR_TEXT_MAX:
        return text
    logger.warning(
        "[MilvusExpr] Truncating %s from %d to %d characters for Milvus insert",
        field,
        len(text),
        MILVUS_VARCHAR_TEXT_MAX,
    )
    return text[:MILVUS_VARCHAR_TEXT_MAX]


def split_text_for_milvus_varchar(
    text: str,
    *,
    max_len: int = MILVUS_VARCHAR_TEXT_MAX,
    overlap: int = 500,
) -> list[str]:
    """Split oversized text into chunks that fit Milvus VARCHAR limits."""
    text = (text or "").strip()
    if not text:
        return []
    if len(text) <= max_len:
        return [text]

    chunks: list[str] = []
    start = 0
    while start < len(text):
        end = min(start + max_len, len(text))
        chunks.append(text[start:end])
        if end >= len(text):
            break
        start = max(end - overlap, start + 1)
    return chunks
