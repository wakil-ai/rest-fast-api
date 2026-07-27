"""Render a chat history message as a downloadable DOCX file.

Uses the system ``pandoc`` binary (via ``pypandoc``) to convert the message's
markdown into DOCX, rather than a hand-rolled markdown parser — pandoc
already handles tables, nested lists, blockquotes, and links correctly.
"""

from __future__ import annotations

import os
import tempfile
from typing import Any

import pypandoc

DOCX_CONTENT_TYPE = (
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
)


def _message_markdown(message: dict[str, Any]) -> str:
    content = message.get("content") or {}
    query = str(content.get("query") or "").strip()
    response = str(content.get("response") or "").strip()

    parts = []
    if query:
        parts.append(f"*{query}*\n\n---")
    parts.append(response)
    return "\n\n".join(p for p in parts if p).strip()


def build_message_docx(message: dict[str, Any]) -> tuple[bytes, str, str]:
    """Convert a chat history message document to DOCX bytes.

    Returns ``(file_bytes, content_type, filename)``. ``message`` is the raw
    Mongo document (must contain a ``content`` dict with ``query``/``response``
    string fields, per ``MessageContent``).
    """
    markdown_text = _message_markdown(message) or " "
    message_id = str(message.get("_id") or message.get("message_id") or "message")

    fd, tmp_path = tempfile.mkstemp(suffix=".docx")
    os.close(fd)
    try:
        pypandoc.convert_text(
            markdown_text,
            to="docx",
            format="gfm",
            outputfile=tmp_path,
        )
        with open(tmp_path, "rb") as f:
            file_bytes = f.read()
    finally:
        os.remove(tmp_path)

    filename = f"{message_id}.docx"
    return file_bytes, DOCX_CONTENT_TYPE, filename
