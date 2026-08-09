"""Activity logs: the immutable record of who did what.

Evidence under Law ZRU-1115, not application state. Append-only: nothing here is
ever updated, archived, or deleted, and corrections are new events.
"""

from datetime import datetime
from enum import Enum
from typing import Any

from pydantic import BaseModel, Field


class ActorType(str, Enum):
    """`system` covers reminders and jobs; without it those events have no author."""

    user = "user"
    agent = "agent"
    system = "system"


class EventType(str, Enum):
    """`object.verb`, a closed set — a typo must not create a new event type."""

    case_created = "case.created"
    case_updated = "case.updated"
    case_state_changed = "case.state_changed"
    case_assigned = "case.assigned"
    case_archived = "case.archived"
    task_created = "task.created"
    task_updated = "task.updated"
    task_state_changed = "task.state_changed"
    task_assigned = "task.assigned"
    task_archived = "task.archived"
    closure_requested = "closure.requested"
    closure_approved = "closure.approved"
    # Reopening is a new event, never an edit of the approval it undoes — the
    # closure that happened stays on the record.
    closure_reopened = "closure.reopened"
    # The Non-Substitution Gate. `ai.draft_generated` is the only event in this
    # enum written by ActorType.agent — with on_behalf_of naming the human who
    # asked, because a machine acts for someone, never on its own account.
    ai_request_submitted = "ai.request_submitted"
    ai_draft_generated = "ai.draft_generated"
    ai_draft_edited = "ai.draft_edited"
    ai_draft_approved = "ai.draft_approved"
    ai_draft_rejected = "ai.draft_rejected"
    ai_generation_failed = "ai.generation_failed"


class ActivityActor(BaseModel):
    id: str
    type: ActorType = ActorType.user
    # Snapshots taken at write time. Roles change and members leave; the log has to
    # read correctly years later without a join.
    role: str | None = None
    name: str | None = None


class ActivityObject(BaseModel):
    type: str
    id: str
    label: str | None = None


class ActivityLogResponse(BaseModel):
    log_id: str
    org_id: str
    occurred_at: datetime
    event_type: EventType
    actor: ActivityActor
    on_behalf_of: str | None = None
    object: ActivityObject
    case_id: str | None = None
    task_id: str | None = None
    payload: dict[str, Any] = Field(default_factory=dict)


class ActivityLogListResponse(BaseModel):
    org_id: str
    logs: list[ActivityLogResponse]
    # Pass back as `before` for the next page; absent when the end is reached.
    next_before: str | None = None
