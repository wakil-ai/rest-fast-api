# app/core/config.py

from pydantic_settings import BaseSettings
from pydantic import ConfigDict
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

class LLMProvider(str, Enum):
    novita = "novita"
    local = "local"
    
class EmbeddingModel(str, Enum):
    qwen = "qwen"
    openai = "openai"
    novita_qwen = "novita_qwen"
    deepinfra = "deepinfra"
    siliconflow = "siliconflow"
    
# SPEECH TO TEXT
class SpeechToTextProvider(str, Enum):
    google = "google"
    azure = "azure"

class Settings(BaseSettings):
    # General
    # App settings
    APP_NAME: str = "WakilAI Chatbot"
    API_PREFIX: str = "/api"
    VERSION: str = "5.0.0"
    DEBUG: bool = False
    TRACING: bool = False # Enable tracing for agents and crews
    
    # Memory Service API Key
    MEM0_API_KEY: str  # Mem

    # VECTOR DBs
    # Vector Database Configuration
    VECTOR_DB_TYPE: VectorDBType = VectorDBType.milvus

    # MongoDB
    MONGODB_URI: Optional[str] = None  # Make optional
    COLLECTION_NAME: Optional[str] = None  # Make optional
    MONGODB_DB_NAME: str = "wakilai"
    USERS_COLLECTION: str = "users"
    SESSIONS_COLLECTION: str = "sessions"
    MESSAGES_COLLECTION: str = "messages"
    FEEDBACK_COLLECTION: str = "feedbacks"

    # Milvus
    MILVUS_MAIN_NAME: str = "lexuz" 
    MILVUS_SOLIQ_ASSISTANT_NAME: str = "soliq"
    MILVUS_URI: str = "http://localhost:19530"
    MILVUS_USER: Optional[str] = None
    MILVUS_PASSWORD: Optional[str] = None

    # Pinecone
    PINECONE_API_KEY: Optional[str] = None
    PINECONE_ENVIRONMENT: Optional[str] = None
    PINECONE_INDEX_NAME: Optional[str] = None
    NAMESPACE_NAME: str = "lexuz"
    
    # LLM
    # Model Selection
    LLM_PROVIDER: LLMProvider = LLMProvider.novita

    # OpenAI GPT
    OPENAI_API_KEY: Optional[str] = None
    GPT_COMPLETION_MODEL: str = "gpt-4o"  # Default GPT model

    # Novita AI API for Gemma provider
    NOVITA_API_BASE: str = "https://api.novita.ai/v3/openai"
    NOVITA_API_KEY: Optional[str] = None
    NOVITA_TINY_MODEL: str = "openai/gpt-oss-120b"
    NOVITA_MODEL: str = "openai/gpt-oss-120b" 

    # Novita Embeddings (OpenAI-compatible)
    NOVITA_EMBEDDING_BASE_URL: str = "https://api.novita.ai/openai"
    NOVITA_EMBEDDING_MODEL: str = "qwen/qwen3-embedding-8b" 

    # DeepInfra Embeddings (OpenAI-compatible)
    DEEPINFRA_API_KEY: Optional[str] = None
    DEEPINFRA_EMBEDDING_BASE_URL: str = "https://api.deepinfra.com/v1/openai"
    DEEPINFRA_EMBEDDING_MODEL: str = "Qwen/Qwen3-Embedding-4B"

    # SiliconFlow Embeddings (OpenAI-compatible)
    SILICONFLOW_API_KEY: Optional[str] = None
    SILICONFLOW_EMBEDDING_BASE_URL: str = "https://api.siliconflow.com/v1/embeddings/"
    SILICONFLOW_EMBEDDING_MODEL: str = "Qwen/Qwen3-Embedding-4B"

    # EMBEDDING MODEL
    EMBEDDING_MODEL: EmbeddingModel = EmbeddingModel.qwen

    SPEECH_TO_TEXT_PROVIDER: SpeechToTextProvider = SpeechToTextProvider.azure
    AZURE_SPEECH_KEY: Optional[str] = None
    AZURE_SPEECH_REGION: Optional[str] = None
    
    # OCR Service
    OCR_API_URL: Optional[str] = 'http://localhost:3030'
    
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
    TEMPERATURE: float = 0.1
    CHAT_HISTORY_LIMIT: int = 5
    OUTPUT_MAX_TOKENS: int = 8192

    LOCAL_VLLM_BASE_URL: str = "http://localhost:8000"
    LOCAL_VLLM_MODEL: str = "gpt-oss-120b"
    LOCAL_VLLM_API_KEY: str = "sk-no-key-required"
    
    # Auth (For Telegram Login)
    TELEGRAM_BOT_TOKEN: str
    TELEGRAM_BOT_LOGIN: str
    TELEGRAM_SESSION_TIMEOUT: int = 86400 * 3 # 3 day in seconds
    
    # Web Scraping
    TAVILY_API_KEY: Optional[str] = None
    
    # EMBEDDING DIM
    @property
    def EMBEDDING_DIM(self) -> int:
        if self.EMBEDDING_MODEL == EmbeddingModel.qwen or self.EMBEDDING_MODEL == EmbeddingModel.deepinfra or self.EMBEDDING_MODEL == EmbeddingModel.siliconflow: # Qwen3-Embedding-4B
            return 2560
        elif self.EMBEDDING_MODEL == EmbeddingModel.openai: # Text-Embedding-Ada-002
            return 1536
        elif self.EMBEDDING_MODEL == EmbeddingModel.novita_qwen: # Qwen3-Embedding-8B
            return 4096
        else:
            raise ValueError(f"Unknown embedding model: {self.EMBEDDING_MODEL}")
       
    model_config = ConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="allow"  # Allow extra fields in the environment
    )

settings = Settings()