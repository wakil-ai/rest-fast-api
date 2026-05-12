"""Resolve short follow-up selections (e.g. ``2``) against the latest assistant message."""

from __future__ import annotations

import re

_FOLLOW_UP_SECTION = re.compile(
    r"(?is)(?:aniqlashtiriluvchi\s+savollar|follow[-\s]*up\s+questions?)\b[^:\n]*:\s*(.*)$"
)
_ASSISTANT_BLOCK = re.compile(
    r"(?ms)^\s*Assistant:\s*(.*?)(?=^\s*Turn\s+\d+:\s*$|^\d+\.\s*User:|\Z)"
)
_TURN_HEADER = re.compile(r"^Turn\s+(\d+):\s*$", re.IGNORECASE)
_USER_LINE = re.compile(r"^\d+\.\s*User:\s*", re.IGNORECASE)
_ASSISTANT_LINE = re.compile(r"^\s*Assistant:\s*(.*)$", re.IGNORECASE)
_CHECKPOINT_ASSISTANT = re.compile(r"^\[(?:ai|assistant)\]\s*$", re.IGNORECASE)
_NUMBERED_ITEM = re.compile(r"^(\d+)\.\s+(.*)$")

_ORDINAL_INDEX: dict[str, int] = {
    "birinchi": 1,
    "ikkinchi": 2,
    "uchinchi": 3,
    "to'rtinchi": 4,
    "tortinchi": 4,
    "first": 1,
    "second": 2,
    "third": 3,
    "fourth": 4,
}


def follow_up_selector_index(query: str) -> int | None:
    """Return 1-based follow-up index when ``query`` is only a selection token."""
    text = (query or "").strip()
    if not text:
        return None
    if re.fullmatch(r"#?\d{1,2}\.?", text):
        return int(text.lstrip("#").rstrip("."))
    lowered = text.lower().replace("’", "'")
    for word, index in _ORDINAL_INDEX.items():
        if re.fullmatch(rf"{re.escape(word)}(?:\s+(?:savol|variant|biri|one))?", lowered):
            return index
    match = re.fullmatch(
        r"(?i)(\d{1,2})\s*(?:-|\.)?\s*(?:savol|variant|son|qadam)?",
        text,
    )
    if match:
        return int(match.group(1))
    match = re.fullmatch(
        r"(?i)(?:the\s+)?(first|second|third|fourth)(?:\s+one)?",
        text,
    )
    if match:
        return _ORDINAL_INDEX[match.group(1).lower()]
    return None


def _parse_numbered_follow_up_options(section: str) -> dict[int, str]:
    options: dict[int, str] = {}
    current_index: int | None = None
    current_lines: list[str] = []
    for raw_line in section.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        if line.startswith("—") or line.lower().startswith("ushbu javob"):
            break
        match = _NUMBERED_ITEM.match(line)
        if match:
            if current_index is not None:
                options[current_index] = " ".join(current_lines).strip()
            current_index = int(match.group(1))
            current_lines = [match.group(2).strip()]
            continue
        if current_index is not None:
            current_lines.append(line)
    if current_index is not None:
        options[current_index] = " ".join(current_lines).strip()
    return {index: text for index, text in options.items() if text}


def extract_numbered_follow_up_options(assistant_text: str) -> dict[int, str]:
    """Parse numbered follow-up options from the tail of an assistant message."""
    text = (assistant_text or "").strip()
    if not text:
        return {}
    match = _FOLLOW_UP_SECTION.search(text)
    section = match.group(1).strip() if match else text
    return _parse_numbered_follow_up_options(section)


def last_assistant_message_from_history(chat_history: str) -> str:
    """Return the most recent assistant message body from formatted chat history."""
    history = (chat_history or "").strip()
    if not history:
        return ""

    checkpoint_blocks: list[str] = []
    current_role: str | None = None
    current_lines: list[str] = []
    for line in history.splitlines():
        if _CHECKPOINT_ASSISTANT.match(line.strip()):
            if current_role == "assistant" and current_lines:
                checkpoint_blocks.append("\n".join(current_lines).strip())
            current_role = "assistant"
            current_lines = []
            continue
        if line.strip().startswith("[") and not _CHECKPOINT_ASSISTANT.match(line.strip()):
            if current_role == "assistant" and current_lines:
                checkpoint_blocks.append("\n".join(current_lines).strip())
            current_role = None
            current_lines = []
            continue
        if current_role == "assistant":
            current_lines.append(line)
    if current_role == "assistant" and current_lines:
        checkpoint_blocks.append("\n".join(current_lines).strip())
    if checkpoint_blocks:
        return checkpoint_blocks[-1]

    turn_blocks = re.split(r"(?m)^Turn\s+\d+:\s*$", history)
    if len(turn_blocks) > 1:
        assistants: list[str] = []
        for block in turn_blocks[1:]:
            for match in _ASSISTANT_BLOCK.finditer(block):
                body = match.group(1).strip()
                if body:
                    assistants.append(body)
        if assistants:
            return assistants[-1]

    legacy_blocks = list(_ASSISTANT_BLOCK.finditer(history))
    if legacy_blocks:
        return legacy_blocks[-1].group(1).strip()

    assistants: list[str] = []
    collecting = False
    lines: list[str] = []
    for line in history.splitlines():
        if _TURN_HEADER.match(line.strip()) or _USER_LINE.match(line):
            if collecting and lines:
                assistants.append("\n".join(lines).strip())
            collecting = False
            lines = []
            continue
        assistant_match = _ASSISTANT_LINE.match(line)
        if assistant_match:
            if collecting and lines:
                assistants.append("\n".join(lines).strip())
            collecting = True
            lines = []
            first = assistant_match.group(1).strip()
            if first:
                lines.append(first)
            continue
        if collecting:
            lines.append(line)
    if collecting and lines:
        assistants.append("\n".join(lines).strip())
    return assistants[-1] if assistants else ""


def resolve_follow_up_selection(query: str, chat_history: str) -> str | None:
    """Expand a short follow-up selection into the chosen question text, if possible."""
    index = follow_up_selector_index(query)
    if index is None:
        return None
    assistant_text = last_assistant_message_from_history(chat_history)
    if not assistant_text:
        return None
    options = extract_numbered_follow_up_options(assistant_text)
    selected = options.get(index)
    if not selected:
        return None
    return selected.strip()
