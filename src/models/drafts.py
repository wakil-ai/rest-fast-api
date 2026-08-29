"""Drafts under the Non-Substitution Gate (ZRU-1115).

A row is one *version* of one draft. Rows are never overwritten: a human edit
inserts a new row and supersedes the old one, so version 1 keeps the machine's
untouched text forever. That permanence is the evidence.

The collection is `drafts`, not `ai_outputs`: only version 1 is the agent's work.
``source`` is what distinguishes agent from human, not the collection name.
"""

from datetime import datetime
from enum import Enum

from pydantic import BaseModel, Field


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
    # The request behind the generation. `query_base` is what the server composed
    # from the Case/Task record; `query_final` is what the employee actually sent.
    # Both are kept so the difference is provable years later — the same reason
    # version 1 of the content is never overwritten. Absent on list endpoints.
    query_base: str | None = None
    query_final: str | None = None
    query_edited: bool = False


class DraftListResponse(BaseModel):
    drafts: list[DraftResponse]


class DraftEditRequest(BaseModel):
    content: str


class DelegateRoleOption(BaseModel):
    """One reasoning role the employee may pick for a delegation.

    `key` is what goes back on the delegate call. It is deliberately the raw
    name and not the canonical assistant: `deepresearch` aliases to `main` in
    AssistantConfig, and collapsing it early is what would lose the deep-analysis
    routing.
    """

    key: str
    label: str
    description: str
    credit_cost: int


class DelegatePrepareRequest(BaseModel):
    """Optional shaping for the composed request.

    `previous_draft_id` is what turns a regeneration into a revision: the earlier
    text comes back inside the composed request, so the employee says what to
    change rather than restating the whole task.
    """

    previous_draft_id: str | None = None
    instruction: str | None = Field(default=None, max_length=20000)
    language: str = Field(
        default="uz",
        description="Language code (uz, ru, en) for the composed request's field "
        "headings — the employee reads and edits this text",
    )


class DelegatePrepareResponse(BaseModel):
    """The request as the machine composed it, for a human to review.

    Reserves no slot and spends no credits: preparing a delegation and walking
    away costs nothing, which is what lets the review step be the default path
    rather than an extra confirmation.
    """

    org_id: str
    case_id: str
    task_id: str | None = None
    query: str = Field(..., description="Composed request, editable before sending")
    base_hash: str = Field(
        ...,
        description="Fingerprint of the composed request; send it back so the "
        "server can tell an edited request from an untouched one",
    )
    assistant: str = Field(..., description="Default reasoning role for this work")
    roles: list[DelegateRoleOption] = Field(default_factory=list)
    closed: bool = Field(
        default=False, description="True when the work is closed and cannot be delegated"
    )


class DelegateRequest(BaseModel):
    """What the employee confirmed in the review step.

    `query` is the reviewed request. When it is absent the server falls back to
    composing one itself, which keeps the older instruction-only clients working.
    """

    instruction: str | None = None
    query: str | None = Field(
        default=None,
        max_length=100000,
        description="The reviewed request text. Length is re-checked server-side.",
    )
    assistant: str | None = Field(
        default=None, description="Reasoning role chosen for this delegation"
    )
    base_hash: str | None = Field(
        default=None,
        description="The `base_hash` from prepare, so an untouched request is not "
        "recorded as a human edit",
    )
    language: str = Field(
        default="uz",
        description="Must match the language passed to prepare. The server "
        "recomposes `query_base` with it, and a mismatch would make an untouched "
        "request look edited.",
    )
