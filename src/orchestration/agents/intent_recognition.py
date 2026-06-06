from typing import Any

from langchain_core.output_parsers import PydanticOutputParser
from pydantic import ValidationError

from src.core.dependencies import get_orchestration_service
from src.core.logger import logger
from src.core.langfuse_tracing import LlmRunName
from src.orchestration.llms import ainvoke_lite_classification_chat
from src.models.intent_types import DomainType, IntentOutput, LegalIntent
from src.orchestration.prompts import PromptRegistry


class IntentClassifier:
    def __init__(self, prompt_registry: PromptRegistry | None = None) -> None:
        self.prompt_registry = prompt_registry or PromptRegistry()
        self.output_parser = PydanticOutputParser(pydantic_object=IntentOutput)
        self.intent_prompt_template = self.prompt_registry.get_prompt(
            "intent_classification"
        ).template

    async def classify_intent(
        self,
        query: str,
        chat_history: str = "",
        file_context: str = "",
        llm: Any | None = None,
        *,
        user_id: str | None = None,
        session_id: str | None = None,
    ) -> tuple[str, Any, LegalIntent]:
        try:
            structured_prompt = self._build_prompt(
                query, chat_history, file_context
            )
            response = await ainvoke_lite_classification_chat(
                llm or get_orchestration_service().lite_llm,
                system_prompt=structured_prompt,
                user_prompt=query,
                run_name=LlmRunName.INTENT_RECOGNITION,
                user_id=user_id,
                session_id=session_id,
            )
            parsed = self._parse_response(response, file_context)
            domain, intent = self._map_to_enums(
                DomainType.CONTRACT.value,
                parsed.intent,
                file_context,
            )
        except Exception as error:
            logger.error(f"[IntentClassifier] Classification failed: {error}")
            domain = DomainType.CONTRACT
            intent = (
                LegalIntent.CONTRACT_RISK_ANALYSIS
                if file_context.strip()
                else LegalIntent.CONTRACT_TEMPLATE_GENERATION
            )

        prompt = self.prompt_registry.get_prompt_for_intent(domain, intent)
        return (domain.value, prompt, intent)

    def _build_prompt(self, query: str, chat_history: str, file_context: str) -> str:
        full_context = f"""
        Retrieval query (expanded; includes session context when needed): {query}
        Uploaded file context: {file_context}
        Chat history: {chat_history}
        """
        return self.intent_prompt_template.format(query=full_context)

    def _parse_response(
        self, response: str, file_context: str
    ) -> IntentOutput:
        try:
            return self.output_parser.parse(response)
        except ValidationError:
            logger.warning("[IntentClassifier] Structured parsing failed, fallback.")
            if file_context.strip():
                return IntentOutput(
                    domain="contract", intent=LegalIntent.CONTRACT_RISK_ANALYSIS.value
                )
            return IntentOutput(
                domain="contract",
                intent=LegalIntent.CONTRACT_TEMPLATE_GENERATION.value,
            )

    def _map_to_enums(
        self,
        domain_str: str,
        intent_str: str,
        file_context: str,
    ) -> tuple[DomainType, LegalIntent]:
        try:
            domain = DomainType(domain_str.lower())
        except ValueError:
            domain = DomainType.GENERAL

        try:
            intent = LegalIntent(intent_str.lower())
        except ValueError:
            if domain == DomainType.CONTRACT:
                intent = (
                    LegalIntent.CONTRACT_RISK_ANALYSIS
                    if file_context
                    else LegalIntent.CONTRACT_TEMPLATE_GENERATION
                )
            else:
                intent = LegalIntent.GENERAL_LEGAL

        return domain, intent
