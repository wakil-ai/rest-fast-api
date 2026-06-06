import hashlib
import html
import re
import unicodedata
from decimal import Decimal, InvalidOperation


# Lightweight text-cleaning helpers (formerly in context_formatter.py)
class TextCleaner:
    """Static text-cleaning utilities shared across assistants."""

    @staticmethod
    def clean_markdown(text: str) -> str:
        text = re.sub(r"(\*\*|\*|__|_)+", "", text)
        text = re.sub(r"([#*-]+)", "", text)
        text = re.sub(r"https?://[^\s]+", "", text)
        text = re.sub(r"buxgalter\.uz", "", text, flags=re.IGNORECASE)
        text = re.sub(r"[\[\]{}()<>]", "", text)
        return text

    @staticmethod
    def remove_header_lines(text: str) -> str:
        cleaned = [le for le in text.splitlines() if not re.match(r"^\s*#{1,6}\s*", le)]
        return "\n".join(cleaned).strip()

    @staticmethod
    def normalize_unicode(text: str) -> str:
        return unicodedata.normalize("NFC", text)


class PathConverter:
    """Path conversion helpers (lex IDs, md→docx)."""

    @staticmethod
    def is_valid_lex_id(s: str) -> bool:
        if not isinstance(s, str):
            return False
        return re.fullmatch(r"-?\d+", s) is not None


def clean_pdf_html_text(text: str) -> str:
    """
    Clean text extracted from PDF/HTML and return pure readable text
    while preserving line separation.
    """
    # decode html entities
    text = html.unescape(text)

    # remove GLYPH artifacts
    text = re.sub(r"GLYPH<[^>]*>", "", text)

    # remove html tags
    text = re.sub(r"<[^>]+>", "", text)

    # normalize spaces
    text = re.sub(r"[ \t]+", " ", text)

    # insert space between lowercase-uppercase
    text = re.sub(r"([а-яқғҳў])([А-ЯҚҒҲЎ])", r"\1 \2", text)

    # insert space between letter-number
    text = re.sub(r"([А-Яа-яҚҒҲЎқғҳў])(\d)", r"\1 \2", text)

    # insert space between number-letter
    text = re.sub(r"(\d)([А-Яа-яҚҒҲЎқғҳў])", r"\1 \2", text)

    # fix repeated spaces
    text = re.sub(r" +", " ", text)

    # normalize lines
    text = re.sub(r"\n+", "\n", text)

    return text.strip()


def clean_html_text(text: str) -> str:
    """
    Basic cleaner for HTML extracted text:
    - Remove extra whitespace
    - Remove multiple blank lines
    - Normalize weird unicode
    - Strip leading/trailing whitespace
    """
    # Remove multiple newlines
    text = re.sub(r"\n+", "\n", text)

    # Remove multiple spaces
    text = re.sub(r"[ \t]+", " ", text)

    # Normalize weird Unicode quotes, dashes etc. (basic normalization)
    text = text.replace("\u201c", '"').replace("\u201d", '"')
    text = text.replace("\u2018", "'").replace("\u2019", "'")
    text = text.replace("\u2013", "-").replace("\u2014", "-")

    return text.strip()


def clean_markdown_text(text: str) -> str:
    """
    Cleaner for Markdown extracted text:
    - Remove weird artifacts
    - Normalize headings if needed
    """
    # Sometimes pymupdf4llm or other tools add weird artifacts
    text = re.sub(r"\[.*?\]\(.*?\)", "", text)  # Remove markdown links
    text = re.sub(r"!\[.*?\]\(.*?\)", "", text)  # Remove markdown images

    text = re.sub(r"\n+", "\n", text)
    text = text.strip()

    return text


def clean_text(text: str) -> str:
    """
    Clean unwanted lines like 'Source: [...]' from the text.
    """
    lines = text.splitlines()
    cleaned_lines = []

    for line in lines:
        stripped_line = line.strip()

        # Remove if line starts with 'Source:' (case-insensitive)
        if stripped_line.lower().startswith("source:"):
            continue

        cleaned_lines.append(stripped_line)

    return "\n".join(cleaned_lines)


def extract_integers(text: str) -> list[int]:
    """Grab all integer numbers from text (e.g., '115, 114' → [115, 114])."""
    return [int(m) for m in re.findall(r"\b\d+\b", text)]


def remove_braces(text: str) -> str:
    """
    Remove everything inside curly braces `{}` including the braces themselves,
    no matter how many times it appears in the text.
    """
    return re.sub(r"\{[^}]*\}", "", text)


def _md5_hex(value: str) -> str:
    return hashlib.md5(value.encode("utf-8")).hexdigest()


def _as_int(value) -> int | None:
    try:
        if value is None:
            return None
        return int(str(value))
    except Exception:
        return None


def _as_decimal(value) -> Decimal | None:
    try:
        if value is None:
            return None
        return Decimal(str(value))
    except (InvalidOperation, ValueError):
        return None
