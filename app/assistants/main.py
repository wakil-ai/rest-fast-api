from app.assistants.base import BaseAssistant
from app.core.config import settings


class MainAssistant(BaseAssistant):
    """General-purpose legal assistant — uses the default hybrid search and standard formatting from BaseAssistant."""

    def __init__(self, collection_name: str = settings.MILVUS_MAIN_NAME):
        super().__init__(collection_name=collection_name)
