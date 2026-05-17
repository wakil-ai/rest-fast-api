import re
from dataclasses import dataclass
from typing import Any

from app.core.dependencies import get_orchestration_service
from app.core.logger import logger
from app.core.langfuse_tracing import LlmRunName
from app.orchestration.llms import ainvoke_lite_classification_chat
from app.orchestration.prompts import PromptRegistry


@dataclass
class CourtRoutingDecision:
    assistant_name: str | None = None
    court_route_tag: str | None = None


class CourtClassifier:
    """Route court queries using a single classification prompt."""

    COURT_TO_ASSISTANT = {
        "criminal": "criminal_court",
        "civil": "civil_court",
        "economic": "economic_court",
        "administrative": "administrative_court",
    }

    BASE_COURT_LABELS = frozenset(COURT_TO_ASSISTANT.keys())

    ADMINISTRATIVE_ROUTE_TAGS = frozenset(
        {
            "administrative_tax_predicting_lawsuit",
            "administrative_tax_appeal_tax_admin",
            "administrative_tax_appeal_court_decision",
            "administrative_general_admin_litigation",
            "administrative_general_judicial_review",
        }
    )

    DEFAULT_ASSISTANT = "administrative_court"
    DEFAULT_ADMIN_ROUTE_TAG = "administrative_general_admin_litigation"

    def __init__(self, prompt_registry: PromptRegistry | None = None) -> None:
        self.prompt_registry = prompt_registry or PromptRegistry()
        self.court_prompt_template = self.prompt_registry.get_prompt(
            "court_classify_prompt"
        ).template

    async def route_query(
        self,
        query: str,
        chat_history: str = "",
        file_context: str = "",
        llm: Any | None = None,
        *,
        user_id: str | None = None,
        session_id: str | None = None,
    ) -> CourtRoutingDecision:
        try:
            response = await ainvoke_lite_classification_chat(
                llm or get_orchestration_service().lite_llm,
                system_prompt=self.court_prompt_template,
                user_prompt=self._build_user_prompt(
                    query, chat_history, file_context
                ),
                run_name=LlmRunName.COURT_ROUTING,
                user_id=user_id,
                session_id=session_id,
            )
            if not isinstance(response, str):
                return CourtRoutingDecision(
                    assistant_name=self.DEFAULT_ASSISTANT,
                    court_route_tag=self.DEFAULT_ADMIN_ROUTE_TAG,
                )

            decision = self._build_routing_decision(response)
            logger.info(
                f"[CourtClassifier] Routed court assistant to {decision.assistant_name} "
                f"(route_tag={decision.court_route_tag})"
            )
            return decision
        except Exception as error:
            logger.error(f"[CourtClassifier] Classification failed: {error}")
            return CourtRoutingDecision(
                assistant_name=self.DEFAULT_ASSISTANT,
                court_route_tag=self.DEFAULT_ADMIN_ROUTE_TAG,
            )

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

    def _build_routing_decision(self, response: str) -> CourtRoutingDecision:
        tag = self._normalize_route_tag(response)
        assistant, canonical_tag = self._resolve_route(tag)
        if assistant:
            return CourtRoutingDecision(
                assistant_name=assistant, court_route_tag=canonical_tag
            )

        logger.warning(
            f"[CourtClassifier] Unexpected classifier output '{response.strip()}'; "
            f"using default assistant '{self.DEFAULT_ASSISTANT}'"
        )
        return CourtRoutingDecision(
            assistant_name=self.DEFAULT_ASSISTANT,
            court_route_tag=self.DEFAULT_ADMIN_ROUTE_TAG,
        )

    @staticmethod
    def _normalize_route_tag(response: str) -> str:
        text = (response or "").strip().lower()
        for raw_line in text.splitlines():
            line = raw_line.strip().strip("`\"'").lstrip("*•-").strip()
            if not line:
                continue
            line = re.sub(r"^(classification|output|answer)\s*:\s*", "", line)
            line = line.rstrip(".,;:")
            return line
        return ""

    def _resolve_route(self, tag: str) -> tuple[str | None, str | None]:
        if not tag:
            return None, None

        if tag in self.BASE_COURT_LABELS:
            if tag == "administrative":
                return (
                    self.COURT_TO_ASSISTANT["administrative"],
                    self.DEFAULT_ADMIN_ROUTE_TAG,
                )
            return self.COURT_TO_ASSISTANT[tag], tag

        compact = re.sub(r"[^a-z_]", "", tag)
        if compact in self.BASE_COURT_LABELS:
            return self._resolve_route(compact)

        if tag in self.ADMINISTRATIVE_ROUTE_TAGS:
            return self.COURT_TO_ASSISTANT["administrative"], tag

        if compact in self.ADMINISTRATIVE_ROUTE_TAGS:
            return self.COURT_TO_ASSISTANT["administrative"], compact

        for known in sorted(self.ADMINISTRATIVE_ROUTE_TAGS, key=len, reverse=True):
            if known in compact or known in tag.replace(" ", ""):
                return self.COURT_TO_ASSISTANT["administrative"], known

        if tag.startswith("administrative_") or compact.startswith("administrative"):
            logger.warning(
                f"[CourtClassifier] Unknown administrative route '{tag}'; "
                f"using {self.DEFAULT_ADMIN_ROUTE_TAG}"
            )
            return (
                self.COURT_TO_ASSISTANT["administrative"],
                self.DEFAULT_ADMIN_ROUTE_TAG,
            )

        return None, None
