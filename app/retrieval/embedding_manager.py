from urllib.parse import urljoin

import requests
from langchain_openai import OpenAIEmbeddings

from app.core.config import EmbeddingModel, settings
from app.core.logger import logger


def get_instruction(query: str) -> str:
    instruction = (
        f"Instruct: Given a legal question from Uzbek Law, retrieve the most relevant legal documents \
        and semantically similar documents to answer the question. \nQuery: {query}"
    )
    return instruction


class BaseEmbedding:
    """Base class for embedding implementations."""

    def embed_query(self, query: str) -> list[float]:
        """Generate embedding for a query with instruction."""
        raise NotImplementedError

    def embed_doc(self, text: str) -> list[float]:
        """Generate embedding for a document without instruction."""
        raise NotImplementedError

    def embed_batch(self, texts: list[str]) -> list[list[float]]:
        """Generate embeddings for a batch of texts, with optional instruction for queries."""
        raise NotImplementedError


# VLLM Implementation
class QwenEmbedding(BaseEmbedding):
    def __init__(self):
        self.api_url = urljoin(settings.QWEN_EMBEDDING_URL, "/v1/embeddings")
        self.rerank_url = urljoin(settings.QWEN_EMBEDDING_URL, "/rerank")
        self.model_name = settings.QWEN_EMBEDDING_MODEL
        self.headers = {
            "accept": "application/json",
            "Content-Type": "application/json",
        }

    def rerank(
        self, query: str, documents: list[str], top_k: int = settings.TOP_K
    ) -> list[dict]:
        payload = {
            "model": self.model_name,
            "query": query,
            "documents": documents,
            "top_n": top_k,
            "truncate_prompt_tokens": -1,
            "additional_data": "rerank_request",
            "priority": 0,
        }
        try:
            response = requests.post(
                self.rerank_url, headers=self.headers, json=payload
            )
            response.raise_for_status()
            return response.json().get("results", [])
        except requests.RequestException as e:
            logger.error(f"Qwen rerank request failed: {e}")
            return []

    def embed_query(self, query: str) -> list[float]:
        return self.embed_doc(
            get_instruction(query)
        )  # Qwen doesn't use instructions for queries

    def embed_doc(self, text: str) -> list[float]:
        response = self._get_response([text])
        return response["data"][0]["embedding"]

    def embed_batch(self, texts: list[str]) -> list[list[float]]:
        if not texts:
            return []
        response = self._get_response(texts)
        return [item["embedding"] for item in response.get("data", [])]

    def _get_payload(self, inputs: list[str]) -> dict:
        return {
            "model": self.model_name,
            "input": inputs,
            "encoding_format": "float",
            "user": "default_user",
            "truncate_prompt_tokens": -1,
            "additional_data": "embedding_request",
            "add_special_tokens": True,
            "priority": 0,
        }

    def _get_response(self, inputs: list[str]) -> dict:
        payload = self._get_payload(inputs)
        try:
            response = requests.post(self.api_url, headers=self.headers, json=payload)
            response.raise_for_status()
            return response.json()
        except requests.RequestException as e:
            logger.error(f"Qwen embedding failed: {e}")
            raise


class NovitaQwenEmbedding(BaseEmbedding):
    def __init__(self):
        base = settings.NOVITA_EMBEDDING_BASE_URL.rstrip("/")
        self.api_url = f"{base}/v1/embeddings"
        self.model_name = settings.NOVITA_EMBEDDING_MODEL
        self.headers = {
            "accept": "application/json",
            "Content-Type": "application/json",
            "Authorization": f"Bearer {settings.NOVITA_API_KEY}",
        }

    def embed_query(self, query: str) -> list[float]:
        return self.embed_doc(get_instruction(query))  # Novita doesn't use instructions

    def embed_doc(self, text: str) -> list[float]:
        payload = {"model": self.model_name, "input": text, "encoding_format": "float"}
        try:
            response = requests.post(self.api_url, headers=self.headers, json=payload)
            response.raise_for_status()
            return response.json()["data"][0]["embedding"]
        except requests.RequestException as e:
            logger.error(f"Novita embedding failed: {e}")
            raise

    def embed_batch(self, texts: list[str]) -> list[list[float]]:
        if not texts:
            return []
        payload = {"model": self.model_name, "input": texts, "encoding_format": "float"}
        try:
            response = requests.post(self.api_url, headers=self.headers, json=payload)
            response.raise_for_status()
            return [item["embedding"] for item in response.json().get("data", [])]
        except requests.RequestException as e:
            logger.error(f"Novita batch embedding failed: {e}")
            raise


class DeepInfraEmbedding(BaseEmbedding):
    def __init__(self):
        base = settings.DEEPINFRA_EMBEDDING_BASE_URL.rstrip("/")
        self.api_url = f"{base}/embeddings"
        self.model_name = settings.DEEPINFRA_EMBEDDING_MODEL
        self.headers = {
            "accept": "application/json",
            "Content-Type": "application/json",
            "Authorization": f"Bearer {settings.DEEPINFRA_API_KEY}",
        }

    def embed_query(self, query: str) -> list[float]:
        return self.embed_doc(get_instruction(query))

    def embed_doc(self, text: str) -> list[float]:
        payload = {"model": self.model_name, "input": text, "encoding_format": "float"}
        try:
            response = requests.post(self.api_url, headers=self.headers, json=payload)
            response.raise_for_status()
            return response.json()["data"][0]["embedding"]
        except requests.RequestException as e:
            logger.error(f"DeepInfra embedding failed: {e}")
            raise

    def embed_batch(self, texts: list[str]) -> list[list[float]]:
        if not texts:
            return []
        payload = {"model": self.model_name, "input": texts, "encoding_format": "float"}
        try:
            response = requests.post(self.api_url, headers=self.headers, json=payload)
            response.raise_for_status()
            return [item["embedding"] for item in response.json().get("data", [])]
        except requests.RequestException as e:
            logger.error(f"DeepInfra batch embedding failed: {e}")
            raise


class OpenAIEmbedding(BaseEmbedding):
    def __init__(self):
        self.embeddings = OpenAIEmbeddings(
            openai_api_key=settings.OPENAI_API_KEY,
            model=settings.OPENAI_EMBEDDING_MODEL,
        )

    def embed_query(self, query: str) -> list[float]:
        try:
            return self.embeddings.embed_query(get_instruction(query))
        except Exception as e:
            logger.error(f"OpenAI query embedding failed: {e}")
            raise

    def embed_doc(self, text: str) -> list[float]:
        try:
            return self.embeddings.embed_query(text)
        except Exception as e:
            logger.error(f"OpenAI document embedding failed: {e}")
            raise

    def embed_batch(
        self, texts: list[str], is_query: bool = False
    ) -> list[list[float]]:
        if not texts:
            return []
        try:
            return self.embeddings.embed_documents(texts)
        except Exception as e:
            logger.error(f"OpenAI batch embedding failed: {e}")
            raise


class SiliconFlowEmbedding(BaseEmbedding):
    """SiliconFlow Embedding implementation for Qwen3-Embedding-4B."""

    def __init__(self):
        self.api_url = settings.SILICONFLOW_EMBEDDING_BASE_URL
        self.api_key = settings.SILICONFLOW_API_KEY
        self.model_name = settings.SILICONFLOW_EMBEDDING_MODEL
        self.headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {self.api_key}",
        }

    def embed_query(self, query: str) -> list[float]:
        return self.embed_doc(get_instruction(query))

    def embed_doc(self, text: str) -> list[float]:
        payload = {"model": self.model_name, "input": text, "encoding_format": "float"}

        try:
            response = requests.post(self.api_url, headers=self.headers, json=payload)
            response.raise_for_status()
            return response.json()["data"][0]["embedding"]
        except requests.RequestException as e:
            logger.error(f"SiliconFlow embedding failed: {e}")
            raise

    def embed_batch(self, texts: list[str]) -> list[list[float]]:
        if not texts:
            return []

        payload = {"model": self.model_name, "input": texts, "encoding_format": "float"}

        try:
            response = requests.post(self.api_url, headers=self.headers, json=payload)
            response.raise_for_status()
            return [item["embedding"] for item in response.json().get("data", [])]
        except requests.RequestException as e:
            logger.error(f"SiliconFlow batch embedding failed: {e}")
            raise


class EmbeddingManager:
    _instance = None
    _initialized = False

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super(EmbeddingManager, cls).__new__(cls)
        return cls._instance

    def __init__(self):
        if self._initialized:
            return

        model_map = {
            EmbeddingModel.novita_qwen: NovitaQwenEmbedding,
            EmbeddingModel.deepinfra: DeepInfraEmbedding,
            EmbeddingModel.qwen: QwenEmbedding,
            EmbeddingModel.openai: OpenAIEmbedding,
            EmbeddingModel.siliconflow: SiliconFlowEmbedding,
        }

        self.embedding = model_map.get(settings.EMBEDDING_MODEL, OpenAIEmbedding)()
        logger.info(
            f"[EmbeddingManager] EmbeddingModel initialized with {settings.EMBEDDING_MODEL} model"
        )
        EmbeddingManager._initialized = True

    def embed_query(self, query: str) -> list[float]:
        return self.embedding.embed_query(query)

    def embed_doc(self, text: str) -> list[float]:
        return self.embedding.embed_doc(text)

    def embed_batch(self, texts: list[str]) -> list[list[float]]:
        return self.embedding.embed_batch(texts)
