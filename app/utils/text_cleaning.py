# app/utils/text_cleaning.py

import re
from typing import List


def clean_html_text(text: str) -> str:
    """
    Basic cleaner for HTML extracted text:
    - Remove extra whitespace
    - Remove multiple blank lines
    - Normalize weird unicode
    - Strip leading/trailing whitespace
    """
    # Remove multiple newlines
    text = re.sub(r'\n+', '\n', text)

    # Remove multiple spaces
    text = re.sub(r'[ \t]+', ' ', text)

    # Normalize weird Unicode quotes, dashes etc. (basic normalization)
    text = text.replace('\u201c', '"').replace('\u201d', '"')
    text = text.replace('\u2018', "'").replace('\u2019', "'")
    text = text.replace('\u2013', '-').replace('\u2014', '-')

    return text.strip()


def clean_markdown_text(text: str) -> str:
    """
    Cleaner for Markdown extracted text:
    - Remove weird artifacts
    - Normalize headings if needed
    """
    # Sometimes pymupdf4llm or other tools add weird artifacts
    text = re.sub(r'\[.*?\]\(.*?\)', '', text)  # Remove markdown links
    text = re.sub(r'!\[.*?\]\(.*?\)', '', text)  # Remove markdown images

    text = re.sub(r'\n+', '\n', text)
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


# Uzbek number words (Latin)
UNITS = ["", "bir", "ikki", "uch", "to'rt", "besh", "olti", "yetti", "sakkiz", "to'qqiz"]
TENS  = ["", "o'n", "yigirma", "o'ttiz", "qirq", "ellik", "oltmish", "yetmish", "sakson", "to'qson"]
SCALES = [
    (10**12, "trillion"),
    (10**9,  "milliard"),
    (10**6,  "million"),
    (10**3,  "ming"),
    (100,     "yuz")
]

def _sub_1_to_999(n: int) -> str:
    parts = []
    hundreds, rem = divmod(n, 100)
    if hundreds:
        parts.append(f"{UNITS[hundreds]} yuz")
    tens, units = divmod(rem, 10)
    if tens:
        parts.append(TENS[tens])
    if units:
        parts.append(UNITS[units])
    return " ".join(parts).strip()

def number_to_uzbek(n: int) -> str:
    """Integer only."""
    if n == 0:
        return "nol"
    sign = "minus " if n < 0 else ""
    n = abs(n)
    words = []
    for value, name in SCALES[:-1]:
        if n >= value:
            count, n = divmod(n, value)
            words.append(_sub_1_to_999(count))
            words.append(name)
    if n:
        words.append(_sub_1_to_999(n))
    return (sign + " ".join(words).strip()).strip()

def replace_numbers_with_uzbek_words(text: str) -> str:
    """
    Replace numbers with Uzbek words.
    Integers: 115 -> bir yuz o'n besh
    Decimals: 3.14 -> uch butun o'n to'rt
    """
    def repl(match):
        num_str = match.group(0)
        # negative?
        negative = num_str.startswith('-')
        if negative:
            num_str = num_str[1:]

        if '.' in num_str:
            left, right = num_str.split('.', 1)
            left_part = number_to_uzbek(int(left))
            right_part = number_to_uzbek(int(right))  # whole number after dot
            res = f"{left_part} butun {right_part}"
        else:
            res = number_to_uzbek(int(num_str))

        if negative:
            res = "minus " + res
        return res

    return re.sub(r'-?\d+(?:\.\d+)?', repl, text)

def extract_integers(text: str) -> List[int]:
        """Grab all integer numbers from text (e.g., '115, 114' → [115, 114])."""
        return [int(m) for m in re.findall(r'\b\d+\b', text)]

def remove_braces(text: str) -> str:
    """
    Remove everything inside curly braces `{}` including the braces themselves,
    no matter how many times it appears in the text.
    """
    return re.sub(r'\{[^}]*\}', '', text)