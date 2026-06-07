from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Header, HTTPException
from pydantic import BaseModel, Field

from core.config import settings
from core.dependencies import get_mongo_handler

router = APIRouter(prefix="/internal", tags=["Internal"])


class CriminalExcerptsRequest(BaseModel):
    doc_ids: list[str] = Field(default_factory=list)


class CriminalExcerptsResponse(BaseModel):
    excerpts: dict[str, dict[str, Any]]


def _verify_internal_token(token: str | None) -> None:
    expected = (settings.LLM_SERVICE_INTERNAL_TOKEN or "").strip()
    if not expected:
        return
    if token != expected:
        raise HTTPException(status_code=401, detail="Invalid internal service token.")


@router.post("/criminal/excerpts", response_model=CriminalExcerptsResponse)
async def criminal_excerpts(
    request: CriminalExcerptsRequest,
    x_internal_token: str | None = Header(default=None),
) -> CriminalExcerptsResponse:
    _verify_internal_token(x_internal_token)

    doc_ids = [str(doc_id).strip() for doc_id in request.doc_ids if str(doc_id).strip()]
    if not doc_ids:
        return CriminalExcerptsResponse(excerpts={})

    mongo = get_mongo_handler()
    criminal_db = mongo.client[settings.CRIMINAL_CASES_MONGODB_DATABASE]

    cases_cursor = criminal_db["cases"].find({"doc_id": {"$in": doc_ids}})
    cases = await cases_cursor.to_list(length=len(doc_ids))
    case_by_doc_id = {str(case.get("doc_id")): _clean_mongo_doc(case) for case in cases}

    sections_cursor = criminal_db["case_sections"].find({"doc_id": {"$in": doc_ids}})
    sections = await sections_cursor.to_list(length=None)
    sections_by_doc_id: dict[str, list[dict[str, Any]]] = {}
    for section in sections:
        sections_by_doc_id.setdefault(str(section.get("doc_id")), []).append(
            _clean_mongo_doc(section)
        )

    excerpts = {
        doc_id: {
            "case": case_by_doc_id.get(doc_id),
            "sections": sections_by_doc_id.get(doc_id, []),
        }
        for doc_id in doc_ids
        if doc_id in case_by_doc_id or doc_id in sections_by_doc_id
    }
    return CriminalExcerptsResponse(excerpts=excerpts)


def _clean_mongo_doc(doc: dict[str, Any]) -> dict[str, Any]:
    cleaned = dict(doc)
    if "_id" in cleaned:
        cleaned["_id"] = str(cleaned["_id"])
    return cleaned


__all__ = ["router"]
