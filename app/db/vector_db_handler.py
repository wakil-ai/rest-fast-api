from abc import ABC, abstractmethod
from typing import List, Dict, Any


class VectorDBHandler(ABC):
    """Abstract base class for vector database handlers."""
    @abstractmethod
    async def upsert_vectors(self, documents: List[Dict[str, Any]], partition_name: str = None) -> None:
        pass

    @abstractmethod
    async def query_dense(self, dense_vector: List[float], top_k: int) -> List[Dict[str, Any]]:
        pass

    @abstractmethod
    async def query_sparse(self, text_query: str, top_k: int) -> List[Dict[str, Any]]:
        pass

    @abstractmethod
    async def query_hybrid(self, dense_vector: List[float], text_query: str, top_k: int, alpha: float) -> List[Dict[str, Any]]:
        pass