from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass
class AgentRequestContext:
    """Inputs for one assistant-agent chat turn."""

    user_id: str
    session_id: str
    message_id: str
    query: str
    assistant: str
    file_ids: list[str] | None = None
    project_id: str | None = None
    stream: bool = False


@dataclass
class AgentState:
    """Mutable state owned by an assistant agent while it handles a turn."""

    request: AgentRequestContext
    resolved_assistant: str
    chat_history: str = ""
    memory_context: str = ""
    file_context: str = ""
    retrieval_context: str = ""
    system_prompt: str = ""
    answer: str = ""
    attachments: list[dict[str, Any]] | None = None
    metadata: dict[str, Any] | None = None
    classified_legal_intent: str | None = None
    court_route_tag: str | None = None
    error: str | None = None


@dataclass
class GenerationContext:
    """All context needed for one chat-agent generation."""

    user_id: str
    context: str
    system_prompt: str
    chat_history: str
    assistant_name: str
    attachments: list[dict[str, Any]] | None = None
    classified_legal_intent: str | None = None
    court_route_tag: str | None = None


@dataclass
class AgentRunResult:
    """Final non-streaming result from an assistant agent."""

    answer: str
    resolved_assistant: str
    metadata: dict[str, Any]
    attachments: list[dict[str, Any]] | None = None
    generation_context: GenerationContext | None = None
