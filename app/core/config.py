# app/core/config.py

from pydantic_settings import BaseSettings
from typing import Optional
import os
from dotenv import load_dotenv
from enum import Enum

# Manually load the .env file from the root project directory
env_path = os.path.abspath(os.path.join(os.path.dirname(__file__), "../..", ".env"))
load_dotenv(dotenv_path=env_path, override=True)

class VectorDBType(str, Enum):
    pinecone = "pinecone"
    milvus = "milvus"

class GemmaProvider(str, Enum):
    novita = "novita"
    local = "local"
    

class EmbeddingModel(str, Enum):
    qwen = "qwen"
    openai = "openai"
    novita_qwen = "novita_qwen"
    deepinfra = "deepinfra"
    infinity = "infinity"
    gemma = "gemma"

class Settings(BaseSettings):
    # General
    # App settings
    APP_NAME: str = "Lex Humblebee CB Chatbot"
    API_PREFIX: str = "/api"
    VERSION: str = "5.0.0"
    DEBUG: bool = False

    # VECTOR DBs
    # Vector Database Configuration
    VECTOR_DB_TYPE: VectorDBType = VectorDBType.milvus

    # MongoDB
    MONGODB_URI: Optional[str] = None  # Make optional
    MONGODB_DB_NAME: Optional[str] = None  # Make optional
    COLLECTION_NAME: str = "itemdocs"

    # Milvus
    MILVUS_COLLECTION_NAME: str = "lexuz" # Do not name it with -
    MILVUS_URI: str = "http://localhost:19530"
    MILVUS_USER: Optional[str] = None
    MILVUS_PASSWORD: Optional[str] = None

    # Pinecone
    PINECONE_API_KEY: Optional[str] = None
    PINECONE_ENVIRONMENT: Optional[str] = None
    PINECONE_INDEX_NAME: Optional[str] = None
    NAMESPACE_NAME: str = "lexuz"

    # MySQL
    MYSQL_ENABLED: bool = False  # Whether to use MySQL or not
    DATABASE_URL: Optional[str] = None  # MySQL connection URL 

    # LLM
    # Model Selection
    GEMMA_PROVIDER: GemmaProvider = GemmaProvider.novita

    # OpenAI GPT
    OPENAI_API_KEY: Optional[str] = None
    GPT_COMPLETION_MODEL: str = "gpt-4o"  # Default GPT model

    # Novita AI API for Gemma provider
    NOVITA_API_KEY: Optional[str] = None
    NOVITA_MODEL: str = "google/gemma-3-27b-it" 

    # Novita Embeddings (OpenAI-compatible)
    NOVITA_EMBEDDING_BASE_URL: str = "https://api.novita.ai/openai"
    NOVITA_EMBEDDING_MODEL: str = "qwen/qwen3-embedding-8b" 

    # DeepInfra Embeddings (OpenAI-compatible)
    DEEPINFRA_API_KEY: Optional[str] = None
    DEEPINFRA_EMBEDDING_BASE_URL: str = "https://api.deepinfra.com/v1/openai"
    DEEPINFRA_EMBEDDING_MODEL: str = "Qwen/Qwen3-Embedding-4B"

    # EMBEDDING MODEL
    EMBEDDING_MODEL: EmbeddingModel = EmbeddingModel.qwen
    
    # OpenAI Embedding Model
    OPENAI_EMBEDDING_MODEL: str = "text-embedding-ada-002"

    # Qwen with vllm
    QWEN_EMBEDDING_URL: str = None  
    QWEN_EMBEDDING_MODEL: str = None

    # SECURITY
    # Docs User
    DOCS_USER: str   # Default user for accessing docs
    DOCS_PASSWORD: str   # Default password for accessing docs

    # API Key Authentication
    API_KEY_NAME: str
    API_KEY: str

    # OTHERS
    STREAM: bool = True  # Whether to use streaming responses
    TOP_K: int = 10
    ALPHA: float = 0.8

    # TEMPERATURE
    TEMPERATURE_GPT: float = 0.1
    TEMPERATURE_GEMMA: float = 0.5

    CHAT_HISTORY_LIMIT: int = 5

    LOCAL_VLLM_BASE_URL: str = "http://localhost:8000"
    LOCAL_VLLM_MODEL: str = "gpt-oss-120b"
    LOCAL_VLLM_API_KEY: str = "sk-no-key-required"
    LOCAL_VLLM_MAX_TOKENS: int = 2048
    
    # EMBEDDING DIM
    @property
    def EMBEDDING_DIM(self) -> int:
        if self.EMBEDDING_MODEL == EmbeddingModel.qwen or self.EMBEDDING_MODEL == EmbeddingModel.deepinfra:
            return 2560
        elif self.EMBEDDING_MODEL == EmbeddingModel.openai:
            return 1536
        elif self.EMBEDDING_MODEL == EmbeddingModel.novita_qwen:
            return 4096
        elif self.EMBEDDING_MODEL == EmbeddingModel.infinity:
            return 1024 # multilingual-e5-large-instruct
        elif self.EMBEDDING_MODEL == EmbeddingModel.gemma:
            return 768 # gemma embedding dimension
        else:
            raise ValueError(f"Unknown embedding model: {self.EMBEDDING_MODEL}")
       
    class Config:
        env_file = ".env"
        env_file_encoding = "utf-8"
        extra = "allow"  # Allow extra fields in the environment

settings = Settings()