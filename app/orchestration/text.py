from __future__ import annotations

from typing import Any


def message_content_to_plain_str(content: Any) -> str:
    """Normalize LangChain/provider message content to plain text."""
    if content is None:
        return ""
    if isinstance(content, str):
        return content.strip()
    if isinstance(content, list):
        return "".join(_content_block_to_text(block) for block in content).strip()
    return str(content).strip()


def history_text_from_state(state: dict[str, Any]) -> str:
    messages = state.get("messages") or []
    lines: list[str] = []
    for message in messages:
        raw = getattr(message, "content", None)
        if raw is None:
            continue
        text = message_content_to_plain_str(raw)
        if text:
            lines.append(f"{message.type}: {text}")
    return "\n".join(lines)


def _content_block_to_text(block: Any) -> str:
    if isinstance(block, str):
        return block
    if isinstance(block, dict) and block.get("type") == "text":
        return str(block.get("text", ""))
    return str(block)
