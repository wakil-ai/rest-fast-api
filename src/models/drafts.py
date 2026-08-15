"""Drafts under the Non-Substitution Gate (ZRU-1115).

A row is one *version* of one draft. Rows are never overwritten: a human edit
inserts a new row and supersedes the old one, so version 1 keeps the machine's
untouched text forever. That permanence is the evidence.

The collection is `drafts`, not `ai_outputs`: only version 1 is the agent's work.
``source`` is what distinguishes agent from human, not the collection name.
"""

from datetime import datetime
from enum import Enum

from pydantic import BaseModel


class DraftStatus(str, Enum):
    """Only ``pending`` has exits a human can trigger."""

    generating = "generating"
    pending = "pending"
    approved = "approved"
    rejected = "rejected"
    superseded = "superseded"
    failed = "failed"


class DraftSource(str, Enum):
    agent = "agent"
    human = "human"


class DraftResponse(BaseModel):
    draft_id: str
    org_id: str
    case_id: str
    task_id: str | None = None
    session_id: str
    message_id: str | None = None
    chain_id: str
    parent_draft_id: str | None = None
    version: int
    source: DraftSource
    status: DraftStatus
    assistant: str
    created_by: str
    decided_by: str | None = None
    decided_at: datetime | None = None
    edited: bool = False
    created_at: datetime
    # Absent on list endpoints, which project it away.
    content: str | None = None


class DraftListResponse(BaseModel):
    drafts: list[DraftResponse]


class DraftEditRequest(BaseModel):
    content: str


class DelegateRequest(BaseModel):
    instruction: str | None = None
