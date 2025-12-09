# app/models/retrieval_models.py

from pydantic import BaseModel
from typing import Any, Dict, Optional
from app.core.config import settings

class MongoFullTextRequest(BaseModel):
    query_text: str

class MongoMetadataRequest(BaseModel):
    filters: Dict[str, Any]

class VectorDBRequest(BaseModel):
    query_text: str
    top_k: Optional[int] = settings.TOP_K
    collection_name: Optional[str] = settings.MILVUS_MAIN_NAME