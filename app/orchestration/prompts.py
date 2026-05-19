from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from langchain_core.prompts import PromptTemplate

from app.core.logger import logger
from app.models.intent_types import DomainType, LegalIntent
from app.utils.tokens import count_tokens

# MODE index -> part stems (without .md); part numbers preserved for stable ordering.
MODE_PARTS: dict[int, list[str]] = {
    0: ["part_03"],
    1: [],
    2: ["part_04", "part_05", "part_06", "part_07"],
    3: ["part_11"],
    4: ["part_12", "part_09"],
    5: ["part_09"],
    6: ["part_08", "part_14"],
    7: ["part_05", "part_09", "part_10"],
    8: ["part_13_slim"],
}

ALWAYS_PARTS: tuple[str, ...] = ("part_00", "part_01", "part_15")

PART_ORDER: tuple[str, ...] = (
    "part_00",
    "part_01",
    "part_02",
    "part_03",
    "part_04",
    "part_05",
    "part_06",
    "part_07",
    "part_08",
    "part_09",
    "part_10",
    "part_11",
    "part_12",
    "part_13",
    "part_13_slim",
    "part_14",
    "part_15",
)

DEFAULT_MODES: tuple[int, ...] = (1,)
FALLBACK_MODES: tuple[int, ...] = (2, 8)

RUNTIME_PLACEHOLDERS = ("retrieved_cases", "context", "chat_history")

MODE_LABELS: dict[int, str] = {
    0: "Pre-trial / investigation tactics",
    1: "Legal consultation",
    2: "Forensic audit / accusation deconstruction",
    3: "Procedural document generation",
    4: "Interrogation / witness preparation",
    5: "Adversarial simulation",
    6: "Outcome forecast / sentencing risk",
    7: "Defense plan roadmap",
    8: "Court practice intelligence (JIE)",
}

_ORCHESTRATION_DIR = Path(__file__).resolve().parent
_PROMPTS_DIR = _ORCHESTRATION_DIR / "prompts"
_CRIMINAL_V2_DIR = _PROMPTS_DIR / "criminal_v2"


class PromptRegistry:
    """Centralized prompt manager for legal orchestration."""

    DEFAULT_INPUT_VARIABLES = ["context", "chat_history"]

    PROMPT_INPUT_VARIABLES_OVERRIDES: dict[str, list[str]] = {
        "intent_classification": ["query"],
        "court_classify_prompt": [],
        "retrieval_query_rewrite": [],
        "cypher_agent": [],
    }

    PROMPT_FILES = {
        "system_prompt": "system_prompt.md",
        "soliq_assistant": "soliq_assistant.md",
        "predicting_lawsuit_result": "predicting_lawsuit_result.md",
        "appeal_tax_administration": "appeal_tax_administration.md",
        "appeal_court_decision": "appeal_court_decision.md",
        "supreme_admin_litigation": "supreme_admin_litigation.md",
        "supreme_judicial_review": "supreme_judicial_review.md",
        "contract_template_generation": "contract_template_generation.md",
        "contract_risk_analysis": "contract_risk_analysis.md",
        "intent_classification": "intent_classification.md",
        "court_classify_prompt": "court_classify_prompt.md",
        "milvus_query_agent": "milvus_agent.md",
        "economic_court": "economic_court.md",
        "civil_court": "civil_court.md",
        "retrieval_query_rewrite": "retrieval_query_rewrite.md",
        "cypher_agent": "cypher_agent.md",
    }

    ASSISTANT_MAPPING = {
        "main": "system_prompt",
        "umumiy": "system_prompt",
        "tax": "soliq_assistant",
        "administrative_court": "supreme_admin_litigation",
        "contract_analyzer": "contract_template_generation",
        "economic_court": "economic_court",
        "civil_court": "civil_court",
    }

    def __init__(self) -> None:
        self.prompts_dir = _PROMPTS_DIR
        self.prompts: dict[str, PromptTemplate] = {}
        self._load_prompts()

    def _load_prompts(self) -> None:
        for key, filename in self.PROMPT_FILES.items():
            filepath = self.prompts_dir / filename
            if not filepath.exists():
                if key == "retrieval_query_rewrite":
                    self.prompts[key] = PromptTemplate(
                        template=(
                            "You are a retrieval query rewriting agent for a legal assistant.\n"
                            "Rewrite the latest user query into a standalone query for legal "
                            "document retrieval.\n"
                            "Do not answer the question. Output only the rewritten retrieval query.\n\n"
                            "Latest user query:\n{query}\n\n"
                            "Recent session history:\n{history}\n\n"
                            "Uploaded file context:\n{file_context}\n\n"
                            "Long-term user memory:\n{long_memory}\n"
                        ),
                        input_variables=["query", "history", "file_context", "long_memory"],
                    )
                    continue
                raise FileNotFoundError(f"Prompt file not found: {filepath}")

            input_variables = self.PROMPT_INPUT_VARIABLES_OVERRIDES.get(
                key, self.DEFAULT_INPUT_VARIABLES
            )
            self.prompts[key] = PromptTemplate(
                template=filepath.read_text(encoding="utf-8"),
                input_variables=input_variables,
            )

    def get_prompt(self, name: str) -> PromptTemplate:
        if name not in self.prompts:
            raise ValueError(f"Prompt '{name}' not found.")
        return self.prompts[name]

    def get_assistant_prompt(self, assistant_name: str) -> PromptTemplate:
        if assistant_name not in self.ASSISTANT_MAPPING:
            raise ValueError(
                f"Prompt template for assistant '{assistant_name}' not found."
            )
        return self.get_prompt(self.ASSISTANT_MAPPING[assistant_name])

    def get_prompt_for_intent(
        self,
        domain: DomainType,
        intent: LegalIntent,
    ) -> PromptTemplate:
        if domain == DomainType.TAX:
            mapping = {
                LegalIntent.PREDICTING_LAWSUIT: "predicting_lawsuit_result",
                LegalIntent.APPEAL_COURT_DECISION: "appeal_court_decision",
                LegalIntent.APPEAL_TAX_ADMIN: "appeal_tax_administration",
            }
            return self.get_prompt(mapping.get(intent, "appeal_tax_administration"))

        if domain == DomainType.GENERAL:
            mapping = {
                LegalIntent.ADMIN_LITIGATION: "supreme_admin_litigation",
                LegalIntent.JUDICIAL_REVIEW: "supreme_judicial_review",
            }
            return self.get_prompt(mapping.get(intent, "supreme_admin_litigation"))

        if domain == DomainType.CONTRACT:
            mapping = {
                LegalIntent.CONTRACT_TEMPLATE_GENERATION: "contract_template_generation",
                LegalIntent.CONTRACT_RISK_ANALYSIS: "contract_risk_analysis",
            }
            return self.get_prompt(mapping.get(intent, "contract_template_generation"))


class CriminalPromptComposer:
    """Build system prompts from criminal_prompt_v2 parts by active MODE(s)."""

    def __init__(self, prompts_root: Path | None = None) -> None:
        base = prompts_root or _CRIMINAL_V2_DIR
        self._parts_dir = base / "parts"
        self._part_cache: dict[str, str] = {}

    @staticmethod
    def format_modes_label(modes: list[int]) -> str:
        if not modes:
            return "MODE 1 — Legal consultation (default)"
        parts: list[str] = []
        for mode in sorted(set(modes)):
            label = MODE_LABELS.get(mode, f"MODE {mode}")
            parts.append(f"MODE {mode} — {label}")
        return "; ".join(parts)

    def resolve_part_stems(self, modes: list[int]) -> list[str]:
        """Union MODE parts with ALWAYS_PARTS; preserve canonical part order."""
        selected: set[str] = set(ALWAYS_PARTS)
        for mode in modes:
            for stem in MODE_PARTS.get(mode, []):
                selected.add(stem)
        return [stem for stem in PART_ORDER if stem in selected]

    def build_body(self, modes: list[int]) -> str:
        stems = self.resolve_part_stems(modes)
        chunks: list[str] = []
        for stem in stems:
            text = self._load_part(stem)
            if text.strip():
                chunks.append(text.rstrip())
        return "\n\n---\n\n".join(chunks)

    def build_template(self, modes: list[int]) -> str:
        """Core/mode rules first, then runtime slots, then legal documents."""
        core_rules = self.build_body(modes)
        modes_label = self.format_modes_label(modes)
        # Placeholders for runtime injection are escaped for str.format below.
        return (
            "# WakilAI — Criminal Court Assistant (Master Prompt v2)\n\n"
            "## CORE RULES TO FOLLOW\n\n"
            f"**Active MODE(s) this turn:** {modes_label}\n\n"
            "Apply the integration protocol, system core, quality control, and "
            "every MODE-specific block in this section. Do not invent articles, "
            "case numbers, or court outcomes.\n\n"
            f"{core_rules}\n\n"
            "---\n\n"
            "## RUNTIME INJECTIONS (SERVER)\n\n"
            "{chat_history}\n\n"
            "---\n\n"
            "## LEGAL DOCUMENTS\n\n"
            "Ground answers in the materials below and in tool results. "
            "Cite **Raqami** when relying on a retrieved case.\n\n"
            "### Retrieved similar criminal cases (this turn)\n\n"
            "{retrieved_cases}\n\n"
            "### Statutes and codified norms (retrieval)\n\n"
            "{context}\n"
        )

    def format_prompt(
        self,
        modes: list[int],
        *,
        retrieved_cases: str,
        context: str,
        chat_history: str,
    ) -> str:
        template = self.build_template(modes)
        return template.format(
            retrieved_cases=retrieved_cases,
            context=context,
            chat_history=chat_history,
        )

    def estimate_tokens(self, modes: list[int]) -> int:
        return count_tokens(self.build_template(modes))

    def log_composition(self, modes: list[int], *, query_preview: str = "") -> None:
        stems = self.resolve_part_stems(modes)
        tokens = self.estimate_tokens(modes)
        logger.info(
            f"[CriminalPromptComposer] modes={modes} parts={stems} ~tokens={tokens} query={query_preview[:120]}"
        )

    def _load_part(self, stem: str) -> str:
        if stem not in self._part_cache:
            path = self._parts_dir / f"{stem}.md"
            if not path.exists():
                raise FileNotFoundError(f"Criminal prompt part not found: {path}")
            self._part_cache[stem] = path.read_text(encoding="utf-8")
        return self._part_cache[stem]


@lru_cache
def get_criminal_prompt_composer() -> CriminalPromptComposer:
    return CriminalPromptComposer()


async def abuild_criminal_system_prompt(
    query: str,
    *,
    context: str,
    chat_history: str,
    modes: list[int] | None = None,
    chat_history_for_classify: str = "",
    file_context: str = "",
    user_id: str | None = None,
    session_id: str | None = None,
) -> tuple[str, tuple[int, ...]]:
    """Classify MODE(s), pre-retrieve cases, compose and format the system prompt."""
    from app.core.dependencies import (
        get_criminal_case_graph_retriever,
        get_criminal_mode_classifier,
    )

    classifier = get_criminal_mode_classifier()
    composer = get_criminal_prompt_composer()

    if modes is None:
        decision = await classifier.classify(
            query,
            chat_history=chat_history_for_classify,
            file_context=file_context,
            user_id=user_id,
            session_id=session_id,
        )
        active_modes = list(decision.modes)
    else:
        active_modes = sorted({m for m in modes if m in MODE_PARTS or m in range(9)})

    retriever = get_criminal_case_graph_retriever()
    try:
        retrieved_cases = await retriever.abuild_context(query)
    except Exception as exc:
        logger.warning(f"Criminal court pre-retrieval failed: {exc}", exc_info=True)
        retrieved_cases = (
            "(Pre-loaded case retrieval failed; use the `search_criminal_case_graph` tool.)"
        )

    composer.log_composition(active_modes, query_preview=query)
    system = composer.format_prompt(
        active_modes,
        retrieved_cases=retrieved_cases,
        context=context,
        chat_history=chat_history,
    )
    return system, tuple(active_modes)
