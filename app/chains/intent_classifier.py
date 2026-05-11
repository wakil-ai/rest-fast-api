from typing import Any

from langchain_core.output_parsers import PydanticOutputParser
from pydantic import ValidationError

from app.core.dependencies import get_classifier_llm, get_prompt_registry
from app.core.logger import logger
from app.models.intent_types import DomainType, IntentOutput, LegalIntent


class IntentClassifier:
    def __init__(self):
        self.prompt_registry = get_prompt_registry()
        self.llm = get_classifier_llm()

        # Structured output parser
        self.output_parser = PydanticOutputParser(pydantic_object=IntentOutput)

        # Intent classification prompt template
        self.intent_prompt_template = self.prompt_registry.get_prompt(
            "intent_classification"
        ).template

    async def classify_intent(
        self,
        query: str,
        chat_history: str = "",
        uploaded_file_context: str = "",
    ) -> tuple[str, Any, LegalIntent]:

        try:
            structured_prompt = self._build_prompt(
                query, chat_history, uploaded_file_context
            )

            response = await self.llm.generate_response(
                user_prompt=query,
                system_prompt=structured_prompt,
                stream=False,
            )

            parsed = self._parse_response(response, uploaded_file_context)

            # Contract-only classifier: never branch to tax/general assistant prompts
            domain, intent = self._map_to_enums(
                DomainType.CONTRACT.value,
                parsed.intent,
                uploaded_file_context,
            )

        except Exception as e:
            logger.error(f"[IntentClassifier] Classification failed: {e}")
            domain = DomainType.CONTRACT
            intent = (
                LegalIntent.CONTRACT_RISK_ANALYSIS
                if uploaded_file_context.strip()
                else LegalIntent.CONTRACT_TEMPLATE_GENERATION
            )

        prompt = self.prompt_registry.get_prompt_for_intent(domain, intent)
        return (domain.value, prompt, intent)

    def _build_prompt(self, query, chat_history, file_context):
        full_context = f"""
        Query: {query}
        Uploaded file context: {file_context}
        Chat history: {chat_history}
        """

        return self.intent_prompt_template.format(query=full_context)

    def _parse_response(self, response: str, uploaded_file_context: str) -> IntentOutput:
        try:
            return self.output_parser.parse(response)
        except ValidationError:
            logger.warning("[IntentClassifier] Structured parsing failed, fallback.")
            if uploaded_file_context.strip():
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
        uploaded_file_context: str,
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
                    if uploaded_file_context
                    else LegalIntent.CONTRACT_TEMPLATE_GENERATION
                )
            else:
                intent = LegalIntent.GENERAL_LEGAL

        return domain, intent
