from app.assistants.base import BaseAssistant
from app.core.config import settings


class EconomicCourtAssistant(BaseAssistant):
    """Economic-court assistant.

    Uses the default hybrid retrieval from BaseAssistant, but with a
    Economic-procedure focused system prompt .
    """

    def __init__(self, collection_name: str = settings.MILVUS_MAIN_NAME):
        super().__init__(collection_name=collection_name)
