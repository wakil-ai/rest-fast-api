from app.assistants.base import BaseAssistant
from app.core.config import settings


class CriminalCourtAssistant(BaseAssistant):
    """Criminal-court assistant.

    Uses the default hybrid retrieval from BaseAssistant, but with a
    criminal-procedure focused system prompt .
    """

    def __init__(self, collection_name: str = settings.MILVUS_CRIMINAL_COURT):
        super().__init__(collection_name=collection_name)
