from app.assistants.base import BaseAgent
from app.core.config import settings


class MainAgent(BaseAgent):
    """General-purpose legal assistant — uses the default hybrid search and standard formatting from BaseAgent."""

    def __init__(self, collection_name: str = settings.MILVUS_MAIN_NAME):
        super().__init__(collection_name=collection_name, assistant_name="main")
