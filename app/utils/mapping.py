from bs4 import Tag as BsTag
from typing import Optional

# ===== Class → Markdown intent mapping =====
CLASS_TO_MD = {
    # Identity / headers
    "ACT_FORM": "#",                 # merged with ACCEPTING_BODY into H1
    "ACCEPTING_BODY": "#",            # see merge logic
    "ACT_TITLE": "##",
    "ACT_TITLE_APPL": "*",
    "PUBLICATION_ORIGIN": "*",
    "NEW_EDITION": "*",

    # Structure
    "TEXT_HEADER_DEFAULT": "###",
    "TEXT_HEADER_AFTER_SRC": "###",
    "CLAUSE_DEFAULT": "####",
    "CLAUSE_AFTER_SRC": "####",

    # Body
    "ACT_TEXT": "",
    "BY_DEFAULT": "",

    # Emphasis / alignment variants
    "TEXT_BOLD": "**",
    "TEXT_BOLD_CENTER": "**",
    "TEXT_BOLD_RIGHT": "**",
    "TEXT_ITALIC": "*",
    "TEXT_CENTER": "",
    "TEXT_RIGHT": "",

    # Notes
    "EXPLANATION": "*",
    "COMMENT": "*",
    "COMMENT_FOR_WARNING": "*",
    "CHANGES_ORIGINS": "*",
    "FOOTNOTE": "*",

    # Essentials (special merged line)
    "ACT_ESSENTIAL_ELEMENTS": "@@@",
    "ACT_ESSENTIAL_ELEMENTS_NUM": "@@@",
}

WORD_HTML_HINTS = (
    "xmlns:w='urn:schemas-microsoft-com:office:word'",
    "xmlns:o='urn:schemas-microsoft-com:office:office'",
    "w:WordDocument",
)

HEADING_FROM_MARK = {"#": "h1", "##": "h2", "###": "h3", "####": "h4"}

# ---------- mapping helpers ----------
def _first_mapped_class(el: BsTag) -> Optional[str]:
    if not isinstance(el, BsTag):
        return None
    classes = (el.get("class") or [])
    if isinstance(classes, str):
        classes = [classes]
    for c in classes:
        if c in CLASS_TO_MD:
            return c
    return None