import re
from dataclasses import dataclass

from app.core.dependencies import get_classifier_llm, get_prompt_registry
from app.core.logger import logger


@dataclass
class CourtRoutingDecision:
    assistant_name: str | None = None


class CourtClassifier:
    """Route court queries using a single classification prompt."""

    COURT_TO_ASSISTANT = {
        "criminal": "criminal_court",
        "civil": "civil_court",
        "economic": "economic_court",
        "administrative": "administrative_court",
    }
    DEFAULT_ASSISTANT = "administrative_court"

    def __init__(self):
        self.prompt_registry = get_prompt_registry()
        self.llm = get_classifier_llm()
        self.court_prompt_template = self.prompt_registry.get_prompt(
            "court_classify_prompt"
        ).template

    async def route_query(
        self,
        query: str,
        chat_history: str = "",
        uploaded_file_context: str = "",
    ) -> CourtRoutingDecision:
        try:
            response = await self.llm.generate_response(
                user_prompt=self._build_user_prompt(
                    query, chat_history, uploaded_file_context
                ),
                system_prompt=self.court_prompt_template,
                stream=False,
            )
            if not isinstance(response, str):
                return CourtRoutingDecision(assistant_name=self.DEFAULT_ASSISTANT)

            decision = self._build_routing_decision(response)
            logger.info(
                f"[CourtClassifier] Routed `court` assistant to {decision.assistant_name}",
            )
            return decision
        except Exception as e:
            logger.error(f"[CourtClassifier] Classification failed: {e}")
            return CourtRoutingDecision(assistant_name=self.DEFAULT_ASSISTANT)

    @staticmethod
    def _build_user_prompt(
        query: str,
        chat_history: str,
        uploaded_file_context: str,
    ) -> str:
        parts = [f"User query:\n{query}"]

        if uploaded_file_context:
            parts.append(f"Uploaded file context:\n{uploaded_file_context}")

        if chat_history:
            parts.append(f"Chat history:\n{chat_history}")

        return "\n\n".join(parts)

    def _build_routing_decision(self, response: str) -> CourtRoutingDecision:
        assistant_name = self._map_response_to_assistant(response)
        if assistant_name:
            return CourtRoutingDecision(assistant_name=assistant_name)

        logger.warning(
            "[CourtClassifier] Unexpected classifier output '%s'; using default assistant '%s'",
            response.strip(),
            self.DEFAULT_ASSISTANT,
        )
        return CourtRoutingDecision(assistant_name=self.DEFAULT_ASSISTANT)

    def _map_response_to_assistant(self, response: str) -> str | None:
        normalized = response.strip().lower()

        for label, assistant_name in self.COURT_TO_ASSISTANT.items():
            if re.fullmatch(rf"{label}", normalized):
                return assistant_name

        compact = re.sub(r"[^a-z]", "", normalized)
        for label, assistant_name in self.COURT_TO_ASSISTANT.items():
            if compact == label:
                return assistant_name

        return None
