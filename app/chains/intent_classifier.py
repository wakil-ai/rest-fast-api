from enum import Enum
from langchain.prompts import PromptTemplate
from app.core.logger import logger
from app.llms.gpt import ChatGPT
from app.chains.prompts import INTENT_CLASSIFICATION_PROMPT
from app.chains.prompts import (
    PROMPT,
    PREDICTING_LAWSUIT_RESULT_PROMPT,
    APPEAL_TO_COURT_DECISION_PROMPT,
    APPEAL_TAX_ADMINISTRATION_PROMPT,
)


class LegalIntent(str, Enum):
    """Legal assistance intent categories"""
    PREDICTING_LAWSUIT = "predicting_lawsuit"  # Predicting lawsuit results
    APPEAL_COURT_DECISION = "appeal_court_decision"  # Appealing court decisions
    APPEAL_TAX_ADMIN = "appeal_tax_admin"  # Appealing tax administration decisions
    GENERAL_LEGAL = "general_legal"  # General legal questions

class IntentClassifier:
    """Classifies user queries into legal assistance intents"""

    def __init__(self):
        self.llm = ChatGPT(model_name="gpt-4o-mini")  # Fast classification model
        
    async def get_prompt_for_intent(self, intent: LegalIntent) -> str:
        """
        Select appropriate system prompt based on classified intent
        
        Args:
            intent: Classified user intent
            
        Returns:
            System prompt string for the specific intent
        """
        prompt_mapping = {
            LegalIntent.PREDICTING_LAWSUIT: PREDICTING_LAWSUIT_RESULT_PROMPT,
            LegalIntent.APPEAL_COURT_DECISION: APPEAL_TO_COURT_DECISION_PROMPT,
            LegalIntent.APPEAL_TAX_ADMIN: APPEAL_TAX_ADMINISTRATION_PROMPT,
            LegalIntent.GENERAL_LEGAL: PROMPT, # Default prompt # Fallback
        }
        
        return prompt_mapping.get(intent, PROMPT)

    async def classify_intent(self, query: str, chat_history: str = "") -> PromptTemplate:
        """
        Classify user query intent

        Args:
            query: User's question
            chat_history: Previous conversation context

        Returns:
            LegalIntent enum value
        """
        try:
            # Build classification prompt with context
            full_query = f"{chat_history}\n\nСўров: {query}" if chat_history else query
            prompt = INTENT_CLASSIFICATION_PROMPT.format(query=full_query)

            # Get classification
            response = await self.llm.generate_response(
                user_prompt=full_query,
                system_prompt=prompt,
                stream=False,
            )

            # Parse response
            intent_str = response.strip().lower()
            
            logger.debug(f"[IntentClassifier] Classification response: {intent_str}")
            
            # Map to enum
            if "predicting_lawsuit" in intent_str:
                intent = LegalIntent.PREDICTING_LAWSUIT
            elif "appeal_court" in intent_str :
                intent = LegalIntent.APPEAL_COURT_DECISION
            elif "appeal_tax" in intent_str:
                intent = LegalIntent.APPEAL_TAX_ADMIN
            else:
                intent = LegalIntent.GENERAL_LEGAL  # Default fallback

            logger.info(f"[IntentClassifier] Query classified as: {intent.value}")
            return await self.get_prompt_for_intent(intent)

        except Exception as e:
            logger.error(f"[IntentClassifier] Classification failed: {e}")
            return await self.get_prompt_for_intent(LegalIntent.GENERAL_LEGAL)
