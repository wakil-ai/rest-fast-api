"""Enterprise Cases.

A Case is a document in the existing `projects` collection, discriminated by
`org_id`. The API says "case" and the id is a `proj-…` string: the whole file →
OCR → Milvus → vector-search pipeline is keyed on `project_id`, and that pipeline
lives partly in a second service, so the concept is renamed at the edge only.
"""

from datetime import date, datetime
from typing import Any

from pydantic import BaseModel, Field

from models.chat import AssistantType
from models.projects import ProjectStatus


class CaseClosure(BaseModel):
    """Who asked for the Case to be closed, and which admin agreed."""

    requested_by: str | None = None
    requested_at: datetime | None = None
    approved_by: str | None = None
    approved_at: datetime | None = None


class CaseCreateRequest(BaseModel):
    title: str = Field(..., min_length=1, max_length=500)
    description: str | None = Field(default=None, max_length=5000)
    objective: str | None = Field(
        default=None,
        max_length=2000,
        description="What a successful outcome looks like; stays visible on the Case",
    )
    case_type: AssistantType | None = Field(
        default=None, description="Which assistant drafts for this Case"
    )
    state_id: str | None = Field(
        default=None, description="Workflow state; the board's draft state if omitted"
    )
    start_date: date | None = None
    deadline: date | None = None
    assignee_id: str | None = Field(
        default=None, description="Must be an active member of the same organization"
    )
    suspect: str | None = Field(default=None, max_length=500)
    victim: str | None = Field(default=None, max_length=500)
    metadata: dict[str, Any] = Field(default_factory=dict)


class CaseUpdateRequest(BaseModel):
    """Partial edit. Omitted keys are untouched; an explicit null clears a field.

    `status` and `closure` are absent by design — they move only through the
    closure endpoints, so that lifecycle has exactly one writer.
    """

    title: str | None = Field(default=None, min_length=1, max_length=500)
    description: str | None = Field(default=None, max_length=5000)
    objective: str | None = Field(default=None, max_length=2000)
    case_type: AssistantType | None = None
    state_id: str | None = None
    start_date: date | None = None
    deadline: date | None = None
    assignee_id: str | None = None
    suspect: str | None = Field(default=None, max_length=500)
    victim: str | None = Field(default=None, max_length=500)
    metadata: dict[str, Any] | None = None


class CaseResponse(BaseModel):
    case_id: str
    org_id: str
    owner_id: str
    title: str
    description: str | None = None
    objective: str | None = None
    case_type: str | None = None
    state_id: str | None = None
    start_date: date | None = None
    deadline: date | None = None
    assignee_id: str | None = None
    suspect: str | None = None
    victim: str | None = None
    status: ProjectStatus
    closure: CaseClosure = Field(default_factory=CaseClosure)
    files: list[str] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)
    stats: dict[str, Any] = Field(default_factory=dict)
    updated_by: str | None = None
    created_at: datetime
    updated_at: datetime


class CaseListResponse(BaseModel):
    org_id: str
    cases: list[CaseResponse]
