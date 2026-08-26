"""Tasks: the assignments a Case is broken into.

Same shape as a Case minus attachments and the suspect/victim parties, per the
spec's §3.3. Its own collection, unlike Cases — nothing existing had to be reused.
"""

from datetime import date, datetime

from pydantic import BaseModel, Field

from models.cases import CaseClosure
from models.chat import AssistantType


class TaskCreateRequest(BaseModel):
    title: str = Field(..., min_length=1, max_length=500)
    description: str | None = Field(default=None, max_length=5000)
    objective: str | None = Field(default=None, max_length=2000)
    task_type: AssistantType | None = None
    state_id: str | None = Field(
        default=None, description="Workflow state; the board's draft state if omitted"
    )
    start_date: date | None = None
    deadline: date | None = None
    assignee_id: str | None = Field(
        default=None, description="Must be an active member of the same organization"
    )


class TaskUpdateRequest(BaseModel):
    """Partial edit. `status`-equivalent lifecycle moves only through closure."""

    title: str | None = Field(default=None, min_length=1, max_length=500)
    description: str | None = Field(default=None, max_length=5000)
    objective: str | None = Field(default=None, max_length=2000)
    task_type: AssistantType | None = None
    state_id: str | None = None
    start_date: date | None = None
    deadline: date | None = None
    assignee_id: str | None = None
    ai_brief: str | None = Field(
        default=None,
        description="AI-generated brief summary. Set by the frontend after /chat/brief returns.",
    )


class TaskResponse(BaseModel):
    task_id: str
    case_id: str
    org_id: str
    title: str
    description: str | None = None
    objective: str | None = None
    task_type: str | None = None
    state_id: str | None = None
    start_date: date | None = None
    deadline: date | None = None
    assignee_id: str | None = None
    ai_brief: str | None = None
    closure: CaseClosure = Field(default_factory=CaseClosure)
    created_by: str
    updated_by: str | None = None
    created_at: datetime
    updated_at: datetime


class TaskListResponse(BaseModel):
    org_id: str
    tasks: list[TaskResponse]
    # Present when the list was scoped to one Case, absent for the org-wide view.
    case_id: str | None = None
