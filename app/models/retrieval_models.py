from typing import Any

from pydantic import BaseModel

from app.core.config import settings


class MongoFullTextRequest(BaseModel):
    query_text: str


class MongoMetadataRequest(BaseModel):
    filters: dict[str, Any]


class VectorDBRequest(BaseModel):
    query_text: str
    top_k: int | None = settings.TOP_K
    collection_name: str | None = settings.MILVUS_MAIN_NAME
