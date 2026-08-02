"""Per-organization workflow boards for Cases and Tasks."""

from enum import Enum

from pydantic import BaseModel, Field


class StateCategory(str, Enum):
    """The fixed meaning behind a user-renamable state.

    A state's ``name`` is whatever the organization typed, in whatever language,
    so no system logic may branch on it. Closure checks, dashboards and reminders
    read this instead, which is what keeps a renamed or custom column from
    breaking them.
    """

    draft = "draft"
    in_progress = "in_progress"
    in_review = "in_review"
    returned = "returned"
    closed = "closed"


class StateAppliesTo(str, Enum):
    """Cases and Tasks keep separate boards."""

    case = "case"
    task = "task"


class WorkflowStateCreateRequest(BaseModel):
    name: str = Field(..., min_length=1, max_length=80)
    category: StateCategory
    applies_to: StateAppliesTo
    order: int | None = Field(
        default=None, description="Board position; appended last when omitted"
    )


class WorkflowStateUpdateRequest(BaseModel):
    """Rename or reposition a state.

    Neither ``category`` nor ``applies_to`` is editable: changing them would
    silently reclassify every Case already sitting in the state — a board column
    could turn from "in progress" into "closed" under the work it holds.
    Re-categorizing means creating a new state and moving items onto it.
    """

    name: str | None = Field(default=None, min_length=1, max_length=80)
    order: int | None = None


class WorkflowStateResponse(BaseModel):
    # No `alias="_id"` here: FastAPI serializes by alias, which would put `_id`
    # on the wire. The service maps `_id` to `state_id` explicitly instead.
    state_id: str
    org_id: str
    name: str
    category: StateCategory
    applies_to: StateAppliesTo
    order: int
    is_system: bool = False


class WorkflowStateListResponse(BaseModel):
    org_id: str
    states: list[WorkflowStateResponse]
