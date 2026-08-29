"""Render a chat message as a downloadable DOCX.

Conversion goes through the system ``pandoc`` binary (via ``pypandoc``)
rather than a hand-rolled markdown parser. The court and contract prompt
templates instruct the model to answer *with tables* — the case-law table
in civil_court.md / economic_court.md, the LEGAL SWOT in
supreme_admin_litigation.md, the risk table in contract_risk_analysis.md —
and those are precisely the answers worth exporting. A regex converter
flattens a GFM pipe table into a run-on paragraph of ``|`` characters;
pandoc turns it into a real Word table, and likewise handles nested
clause numbering, blockquotes and inline emphasis inside cells.

utils/contract_docx.py stays as-is: it backs the contract_analyzer's
auto-attached shartnoma.docx and is not in this path.
"""

from __future__ import annotations

import os
import tempfile
from typing import Any

import pypandoc

DOCX_CONTENT_TYPE = (
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
)

# The model is prompted in GFM, so pipe tables and task lists parse as intended.
_SOURCE_FORMAT = "gfm"

DOCX_FILENAME = "javob.docx"


def message_markdown(message: dict[str, Any]) -> str:
    """The markdown to render: the question, then the answer.

    Including the query keeps the downloaded file self-contained — a lawyer
    filing it away later can see what was actually asked, without it having
    to be reconstructed from the chat.
    """
    content = message.get("content")
    if isinstance(content, dict):
        query = str(content.get("query") or "").strip()
        response = str(content.get("response") or "").strip()
    else:
        # Legacy rows stored content as a bare string (the response only).
        query = ""
        response = str(content or "").strip()

    parts = []
    if query:
        parts.append(f"*{query}*")
        parts.append("---")
    if response:
        parts.append(response)

    return "\n\n".join(parts).strip()


def build_message_docx(message: dict[str, Any]) -> bytes:
    """Convert a chat message document to DOCX bytes.

    ``message`` is the raw Mongo document. Callers must run this off the
    event loop (it shells out to pandoc); see the route for the
    asyncio.to_thread wrapper.
    """
    # pandoc writes binary formats to a file rather than to stdout, so the
    # temp file is required, not incidental.
    markdown_text = message_markdown(message) or " "

    fd, tmp_path = tempfile.mkstemp(suffix=".docx")
    os.close(fd)
    try:
        pypandoc.convert_text(
            markdown_text,
            to="docx",
            format=_SOURCE_FORMAT,
            outputfile=tmp_path,
        )
        with open(tmp_path, "rb") as handle:
            return handle.read()
    finally:
        os.remove(tmp_path)


DRAFT_DOCX_FILENAME = "loyiha.docx"


def draft_markdown(draft: dict[str, Any], holder_title: str | None = None) -> str:
    """The markdown to render for an approved draft.

    Built from the draft row's own ``content``, never from the chat message it
    came out of. A human edit stores ``message_id: None`` on the new version, so
    rendering the message would export the machine's original wording for exactly
    the drafts a person took the trouble to correct — the one document the
    Non-Substitution Gate exists to prevent.
    """
    body = str(draft.get("content") or "").strip()
    request = str(draft.get("query_final") or "").strip()

    parts: list[str] = []
    if holder_title:
        parts.append(f"# {holder_title}")
    if request:
        parts.append(f"*{request}*")
        parts.append("---")
    if body:
        parts.append(body)
    return "\n\n".join(parts).strip()


def build_markdown_docx(markdown_text: str) -> bytes:
    """Convert markdown to DOCX bytes via pandoc.

    Callers must run this off the event loop — pandoc is a subprocess, and it
    stalls every other request on the worker for the length of the conversion.
    """
    fd, tmp_path = tempfile.mkstemp(suffix=".docx")
    os.close(fd)
    try:
        pypandoc.convert_text(
            markdown_text or " ",
            to="docx",
            format=_SOURCE_FORMAT,
            outputfile=tmp_path,
        )
        with open(tmp_path, "rb") as handle:
            return handle.read()
    finally:
        os.remove(tmp_path)
