#!/usr/bin/env python3
"""Run criminal-case LangGraph retrieval (and optional answer) using the REST API stack."""

import argparse
import sys
from pathlib import Path

project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

from app.services.criminal_case_graph_retrieval import CriminalCaseGraphRetriever


def main() -> None:
    parser = argparse.ArgumentParser(description="Criminal case retrieval / criminal-court answer.")
    parser.add_argument(
        "question",
        nargs="*",
        help="Question (default: sample fraud query).",
    )
    parser.add_argument(
        "--answer",
        action="store_true",
        help="After retrieval, call the criminal-court system prompt + generation LLM.",
    )
    args = parser.parse_args()

    q = (
        " ".join(args.question).strip()
        or "Firibgarlik bo'yicha masalalar haqida ma'lumotlar chiqarib berish kerak"
    )
    r = CriminalCaseGraphRetriever()
    if args.answer:
        print(r.answer_with_retrieval(q))
        return
    print(r.build_context(q))


if __name__ == "__main__":
    main()
