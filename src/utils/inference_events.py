from typing import Any
from urllib.parse import urlsplit

from pydantic import BaseModel, Field, ValidationError, field_validator


# Public projections intentionally omit passages, storage identifiers and raw errors.
class RetrievalRoute(BaseModel):
    phase: str
    assistant: str
    task: str
    targets: list[str]
    court_route: str | None = None
    legal_intent: str | None = None


class RetrievalSource(BaseModel):
    source_id: str
    title: str
    target: str
    url: str | None = None
    authority: str = "unknown"
    temporal_status: str = "unknown"
    selection_reason: str = "baseline"

    @field_validator("url")
    @classmethod
    def safe_url(cls, value: str | None) -> str | None:
        if not value:
            return None
        try:
            parsed = urlsplit(value)
            if (parsed.scheme not in {"https", "http"} or not parsed.hostname
                    or parsed.username or parsed.password):
                return None
        except ValueError:
            return None
        return value


class RetrievalSearch(BaseModel):
    target: str
    status: str
    candidate_count: int = Field(default=0, ge=0)


class RetrievalSources(BaseModel):
    phase: str
    sources: list[RetrievalSource] = Field(default_factory=list)
    searches: list[RetrievalSearch] = Field(default_factory=list)
    selected_ids: list[str] = Field(default_factory=list)
    excluded_ids: list[str] = Field(default_factory=list)
    truncated: bool = False


def normalize_inference_event(item: dict[str, Any]) -> dict[str, Any] | None:
    kind = item.get("type")
    v2 = item.get("schema_version") == 2
    data = item.get("data") if v2 else item
    if not isinstance(data, dict):
        raise ValueError("Invalid inference event payload")
    if kind in {"route", "sources"}:
        model = RetrievalRoute if kind == "route" else RetrievalSources
        try:
            projection = model.model_validate(data).model_dump()
        except ValidationError:
            # Visibility is ancillary; malformed metadata must not discard an answer.
            return None
        return {**projection, "type": kind}
    if not v2:
        return item
    if kind == "metadata":
        return {"type": kind, "selected_assistant": data.get("selected_assistant"),
                **{k: item[k] for k in ("run_id", "attempt_id", "revision") if k in item}}
    if kind == "chunk":
        if not isinstance(data.get("chunk"), str):
            raise ValueError("Invalid inference answer chunk")
        return {"type": kind, "chunk": data["chunk"]}
    if kind == "progress":
        return {"type": kind, **{k: data[k] for k in ("stage", "state", "outcome", "iteration") if k in data}}
    if kind == "attachments":
        return {"type": kind, "attachments": data.get("attachments", [])}
    if kind == "error":
        return {"type": kind, "error": data.get("message") or "Inference failed",
                "code": data.get("code"), "retryable": data.get("retryable", False)}
    if kind == "end":
        if data.get("status") not in {"generated", "budget_exhausted"}:
            return {"type": "error", "error": "The answer was not completed. Please try again.",
                    "code": "INFERENCE_NOT_COMPLETED", "status": data.get("status")}
        return {"type": "end", "status": data["status"], "usage_ledger": data.get("usage")}
    # Revision/snapshot/commit controls are deliberately not part of this rollout.
    return None


def merge_sources(previous: dict | None, incoming: dict) -> dict:
    if not previous or incoming["phase"] != "tool_update":
        result = dict(incoming)
    else:
        sources = {s["source_id"]: s for s in previous["sources"]}
        sources.update({s["source_id"]: s for s in incoming["sources"]})
        searches = list(previous["searches"])
        searches.extend(s for s in incoming["searches"] if s not in searches)
        excluded = list(dict.fromkeys(previous["excluded_ids"] + incoming["excluded_ids"]))
        result = {
            **incoming, "sources": list(sources.values()), "searches": searches,
            "selected_ids": [s for s in dict.fromkeys(previous["selected_ids"] + incoming["selected_ids"]) if s not in excluded],
            "excluded_ids": excluded,
            "truncated": previous["truncated"] or incoming["truncated"],
        }
    result.pop("type", None)
    result["truncated"] = result["truncated"] or len(result["sources"]) > 100
    result["sources"] = result["sources"][:100]
    return result
