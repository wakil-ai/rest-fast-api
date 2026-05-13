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
    return "\n".join(
        f"{message.type}: {message.content}"
        for message in messages
        if getattr(message, "content", None)
    )


def _content_block_to_text(block: Any) -> str:
    if isinstance(block, str):
        return block
    if isinstance(block, dict) and block.get("type") == "text":
        return str(block.get("text", ""))
    return str(block)
