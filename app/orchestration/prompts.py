from pathlib import Path

from langchain_core.prompts import PromptTemplate

from app.models.intent_types import DomainType, LegalIntent


class PromptRegistry:
    """Centralized prompt manager for legal orchestration."""

    DEFAULT_INPUT_VARIABLES = ["context", "chat_history"]

    PROMPT_INPUT_VARIABLES_OVERRIDES: dict[str, list[str]] = {
        "intent_classification": ["query"],
        "court_classify_prompt": [],
        "retrieval_query_rewrite": [],
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
        "criminal_court": "criminal_court.md",
        "economic_court": "economic_court.md",
        "civil_court": "civil_court.md",
        "retrieval_query_rewrite": "retrieval_query_rewrite.md",
    }

    ASSISTANT_MAPPING = {
        "main": "system_prompt",
        "umumiy": "system_prompt",
        "tax": "soliq_assistant",
        "administrative_court": "supreme_admin_litigation",
        "contract_analyzer": "contract_template_generation",
        "criminal_court": "criminal_court",
        "economic_court": "economic_court",
        "civil_court": "civil_court",
    }

    def __init__(self) -> None:
        self.prompts_dir = Path(__file__).resolve().parent / "prompts"
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

        return self.get_prompt("system_prompt")
