from app.assistants.base import BaseAssistant
from app.core.config import settings


class CivilCourtAssistant(BaseAssistant):
    """Civil-court assistant.

    Uses the default hybrid retrieval from BaseAssistant, but with a
    Civil-procedure focused system prompt .
    """

    def __init__(self, collection_name: str = settings.MILVUS_CIVIL_COURT):
        super().__init__(collection_name=collection_name)
