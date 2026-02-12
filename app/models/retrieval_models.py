from typing import Any

from pydantic import BaseModel
from dataclasses import dataclass, field

from app.core.config import settings

# Shared value objects
@dataclass
class RetrievalConfig:
    """Immutable configuration for a single retrieval call."""
    top_k: int = settings.TOP_K
    alpha: float = settings.ALPHA
    search_type: str = "hybrid"
    collection_name: str = settings.MILVUS_MAIN_NAME
    filter: str = ""

@dataclass
class RetrievalResult:
    """Standard return type from assistant retrieval."""
    context: str = ""
    attachments: list[dict[str, Any]] = field(default_factory=list)
    prompt_template: Any = None  # Optional PromptTemplate override from assistant


class MongoFullTextRequest(BaseModel):
    query_text: str


class MongoMetadataRequest(BaseModel):
    filters: dict[str, Any]


class VectorDBRequest(BaseModel):
    query_text: str
    top_k: int | None = settings.TOP_K
    collection_name: str | None = settings.MILVUS_MAIN_NAME
