"""Classify criminal-court queries into MODE(s) for prompt composition."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from src.core.dependencies import get_orchestration_service
from src.core.langfuse_tracing import LlmRunName
from src.core.logger import logger
from src.orchestration.llms import ainvoke_lite_classification_chat
from src.orchestration.prompts import DEFAULT_MODES, FALLBACK_MODES

_CLASSIFY_PROMPT_PATH = (
    Path(__file__).resolve().parent.parent / "prompts" / "criminal_mode_classify.md"
)
_VALID_MODES = frozenset(range(9))


@dataclass(frozen=True, slots=True)
class CriminalModeDecision:
    modes: tuple[int, ...]
    procedural_stage: str | None = None


class CriminalModeClassifier:
    """Lite-LLM router: user query → MODE list for CriminalPromptComposer."""

    def __init__(self) -> None:
        self._system_prompt = _CLASSIFY_PROMPT_PATH.read_text(encoding="utf-8")

    async def classify(
        self,
        query: str,
        chat_history: str = "",
        file_context: str = "",
        llm: Any | None = None,
        *,
        user_id: str | None = None,
        session_id: str | None = None,
    ) -> CriminalModeDecision:
        try:
            response = await ainvoke_lite_classification_chat(
                llm or get_orchestration_service().lite_llm,
                system_prompt=self._system_prompt,
                user_prompt=self._build_user_prompt(
                    query, chat_history, file_context
                ),
                run_name=LlmRunName.CRIMINAL_MODE_ROUTING,
                user_id=user_id,
                session_id=session_id,
            )
            if not isinstance(response, str):
                return self._default_decision()
            decision = self._parse_response(response)
            logger.info(f"[CriminalModeClassifier] modes={decision.modes} stage={decision.procedural_stage}")
            return decision
        except Exception as exc:
            logger.warning(f"[CriminalModeClassifier] classification failed: {exc}")
            return CriminalModeDecision(modes=FALLBACK_MODES)

    @staticmethod
    def _build_user_prompt(
        query: str,
        chat_history: str,
        file_context: str,
    ) -> str:
        parts = [f"User query:\n{query}"]
        if file_context:
            parts.append(f"Uploaded file context:\n{file_context}")
        if chat_history:
            parts.append(f"Chat history:\n{chat_history}")
        return "\n\n".join(parts)

    def _parse_response(self, response: str) -> CriminalModeDecision:
        text = (response or "").strip()
        payload = self._extract_json(text)
        if payload is None:
            modes = self._parse_modes_from_text(text)
            if modes:
                return CriminalModeDecision(modes=tuple(sorted(modes)))
            return self._default_decision()

        raw_modes = payload.get("modes")
        if not isinstance(raw_modes, list):
            return self._default_decision()

        modes: list[int] = []
        for item in raw_modes:
            try:
                mode = int(item)
            except (TypeError, ValueError):
                continue
            if mode in _VALID_MODES:
                modes.append(mode)

        if not modes:
            return self._default_decision()

        stage = payload.get("procedural_stage")
        procedural_stage = (
            str(stage).strip() if isinstance(stage, str) and stage.strip() else None
        )
        return CriminalModeDecision(
            modes=tuple(sorted(set(modes))),
            procedural_stage=procedural_stage,
        )

    @staticmethod
    def _extract_json(text: str) -> dict[str, Any] | None:
        fenced = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.DOTALL)
        if fenced:
            try:
                return json.loads(fenced.group(1))
            except json.JSONDecodeError:
                pass
        start = text.find("{")
        end = text.rfind("}")
        if start >= 0 and end > start:
            try:
                return json.loads(text[start : end + 1])
            except json.JSONDecodeError:
                return None
        return None

    @staticmethod
    def _parse_modes_from_text(text: str) -> list[int]:
        found: list[int] = []
        for match in re.finditer(r"\bMODE\s*([0-8])\b", text, re.IGNORECASE):
            mode = int(match.group(1))
            if mode not in found:
                found.append(mode)
        return found

    @staticmethod
    def _default_decision() -> CriminalModeDecision:
        return CriminalModeDecision(modes=DEFAULT_MODES)
