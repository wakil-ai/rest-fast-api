from enum import Enum
import json
import re

from langchain.prompts import PromptTemplate

from app.chains.prompts import (
    APPEAL_TAX_ADMINISTRATION_PROMPT,
    APPEAL_TO_COURT_DECISION_PROMPT,
    INTENT_CLASSIFICATION_PROMPT,
    PREDICTING_LAWSUIT_RESULT_PROMPT,
    PROMPT,
    SUPREME_ADMIN_LITIGATION_SYSTEM_PROMPT_TEMPLATE,
    SUPREME_JUDICIAL_REVIEW_DIRECTIVE,
)
from app.core.logger import logger
from app.llms.gpt import ChatGPT


class DomainType(str, Enum):
    """Legal domain classification"""
    TAX = "tax"  # Tax-related matters
    GENERAL = "general"  # General administrative matters


class LegalIntent(str, Enum):
    """Legal assistance intent categories"""

    # Tax domain intents
    PREDICTING_LAWSUIT = "predicting_lawsuit"  # Predicting lawsuit results
    APPEAL_COURT_DECISION = "appeal_court_decision"  # Appealing court decisions
    APPEAL_TAX_ADMIN = "appeal_tax_admin"  # Appealing tax administration decisions
    
    # General domain intents
    ADMIN_LITIGATION = "admin_litigation"  # General administrative litigation
    JUDICIAL_REVIEW = "judicial_review"  # Appeals to higher courts
    
    GENERAL_LEGAL = "general_legal"  # Fallback for unclear cases


class IntentClassifier:
    """Classifies user queries into legal assistance intents"""

    def __init__(self):
        self.llm = ChatGPT(model_name="gpt-4o-mini")  # Fast classification model

    async def get_prompt_for_intent(self, domain: DomainType, intent: LegalIntent) -> str:
        """
        Select appropriate system prompt based on domain and classified intent

        Args:
            domain: Classified domain (tax or general)
            intent: Classified user intent

        Returns:
            System prompt string for the specific intent
        """
        # Tax domain prompts
        if domain == DomainType.TAX:
            prompt_mapping = {
                LegalIntent.PREDICTING_LAWSUIT: PREDICTING_LAWSUIT_RESULT_PROMPT,
                LegalIntent.APPEAL_COURT_DECISION: APPEAL_TO_COURT_DECISION_PROMPT,
                LegalIntent.APPEAL_TAX_ADMIN: APPEAL_TAX_ADMINISTRATION_PROMPT,
            }
            return prompt_mapping.get(intent, APPEAL_TAX_ADMINISTRATION_PROMPT)
        
        # General domain prompts
        elif domain == DomainType.GENERAL:
            prompt_mapping = {
                LegalIntent.ADMIN_LITIGATION: SUPREME_ADMIN_LITIGATION_SYSTEM_PROMPT_TEMPLATE,
                LegalIntent.JUDICIAL_REVIEW: SUPREME_JUDICIAL_REVIEW_DIRECTIVE,
            }
            return prompt_mapping.get(intent, SUPREME_ADMIN_LITIGATION_SYSTEM_PROMPT_TEMPLATE)
        
        # Fallback
        return PROMPT

    async def classify_intent(
        self, query: str, chat_history: str = "", uploaded_file_context: str = ""
    ) -> tuple[str, str]:
        """
        Classify user query intent using two-stage classification

        Args:
            query: User's question
            uploaded_file_context: Context from uploaded files
            chat_history: Previous conversation context

        Returns:
            Tuple of (domain_type, prompt_template)
            - domain_type: "tax" or "general" for collection routing
            - prompt_template: Appropriate system prompt template
        """
        try:
            # Build classification prompt with context
            full_query = f"Сўров: {query} \n\n Yuklangan Fayl Konteksti: {uploaded_file_context} \n\n Chat tarixi: {chat_history}"
            prompt = INTENT_CLASSIFICATION_PROMPT.format(query=full_query)

            # Get classification
            response = await self.llm.generate_response(
                user_prompt=full_query,
                system_prompt=prompt,
                stream=False,
            )

            # Parse JSON response
            response_str = response.strip()
            logger.debug(f"[IntentClassifier] Raw classification response: {response_str}")

            # Parse domain and intent from JSON
            domain = DomainType.GENERAL  # Default
            intent = LegalIntent.GENERAL_LEGAL  # Default

            try:
                # Try to extract JSON from response (handle cases where LLM adds extra text)
                json_match = re.search(r'\{[^}]+\}', response_str)
                if json_match:
                    json_str = json_match.group(0)
                    classification = json.loads(json_str)
                    
                    domain_str = classification.get("domain", "general").lower()
                    intent_str = classification.get("intent", "").lower()
                    
                    logger.debug(f"[IntentClassifier] Parsed JSON - domain: {domain_str}, intent: {intent_str}")
                    
                    # Map domain
                    if "tax" in domain_str:
                        domain = DomainType.TAX
                        # Map tax intents
                        if "predicting_lawsuit" in intent_str:
                            intent = LegalIntent.PREDICTING_LAWSUIT
                        elif "appeal_court" in intent_str:
                            intent = LegalIntent.APPEAL_COURT_DECISION
                        elif "appeal_tax" in intent_str:
                            intent = LegalIntent.APPEAL_TAX_ADMIN
                        else:
                            intent = LegalIntent.APPEAL_TAX_ADMIN  # Default for tax
                    else:
                        domain = DomainType.GENERAL
                        # Map general intents
                        if "judicial_review" in intent_str:
                            intent = LegalIntent.JUDICIAL_REVIEW
                        elif "admin_litigation" in intent_str:
                            intent = LegalIntent.ADMIN_LITIGATION
                        else:
                            intent = LegalIntent.ADMIN_LITIGATION  # Default for general
                else:
                    # Fallback: try old format "domain:intent"
                    logger.warning(f"[IntentClassifier] No JSON found, trying fallback parsing")
                    if ":" in response_str:
                        parts = response_str.split(":")
                        domain_str = parts[0].strip().lower()
                        intent_str = parts[1].strip().lower()
                    else:
                        domain_str = "tax" if "tax" in response_str.lower() else "general"
                        intent_str = response_str.lower()
                    
                    # Map domain using fallback
                    if "tax" in domain_str:
                        domain = DomainType.TAX
                        if "predicting_lawsuit" in intent_str:
                            intent = LegalIntent.PREDICTING_LAWSUIT
                        elif "appeal_court" in intent_str:
                            intent = LegalIntent.APPEAL_COURT_DECISION
                        elif "appeal_tax" in intent_str:
                            intent = LegalIntent.APPEAL_TAX_ADMIN
                        else:
                            intent = LegalIntent.APPEAL_TAX_ADMIN
                    else:
                        domain = DomainType.GENERAL
                        if "judicial_review" in intent_str:
                            intent = LegalIntent.JUDICIAL_REVIEW
                        elif "admin_litigation" in intent_str:
                            intent = LegalIntent.ADMIN_LITIGATION
                        else:
                            intent = LegalIntent.ADMIN_LITIGATION
                            
            except json.JSONDecodeError as je:
                logger.error(f"[IntentClassifier] JSON parsing failed: {je}, using fallback")
                # Use default values already set above

            logger.info(f"[IntentClassifier] Query classified as: {domain.value}:{intent.value}")
            prompt = await self.get_prompt_for_intent(domain, intent)
            return (domain.value, prompt)

        except Exception as e:
            logger.error(f"[IntentClassifier] Classification failed: {e}")
            prompt = await self.get_prompt_for_intent(DomainType.GENERAL, LegalIntent.GENERAL_LEGAL)
            return (DomainType.GENERAL.value, prompt)
