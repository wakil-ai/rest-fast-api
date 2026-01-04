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
    DEVELOPMENT_MODE: bool = False  # Enable development mode to send retrieved contents and logs to UI
    TRACING: bool = False # Enable tracing for agents and crews
    
    # Memory Service API Key
    MEM0_API_KEY: str = None # Mem
    MEM0_PROJECT_ID: str = None 
    MEM0_ORG_ID: str = None

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
    FILES_COLLECTION: str = "files"
    
    # Google Cloud Storage
    GCS_BUCKET_NAME: Optional[str] = None
    GCS_CREDENTIALS_PATH: Optional[str] = None  # Path to service account JSON file
    GCS_PROJECT_ID: Optional[str] = None
    
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
    GPT_COMPLETION_MODEL: str = "gpt-4.1"  # Default GPT model
    
    # Anthropic Claude
    ANTHROPIC_API_KEY: Optional[str] = None

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
    DATALAB_API_KEY: Optional[str] = None
    
    # OpenAI Embedding Model
    OPENAI_EMBEDDING_MODEL: str = "text-embedding-ada-002"

    # Qwen with vllm
    QWEN_EMBEDDING_URL: Optional[str] = None  
    QWEN_EMBEDDING_MODEL: Optional[str] = None

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
    OUTPUT_MAX_TOKENS: int = 4096

    LOCAL_VLLM_BASE_URL: str = "http://localhost:8000"
    LOCAL_VLLM_MODEL: str = "gpt-oss-120b"
    LOCAL_VLLM_API_KEY: str = "sk-no-key-required"
    
    # Auth (For Telegram Login)
    TELEGRAM_BOT_TOKEN: str
    TELEGRAM_BOT_LOGIN: str
    TELEGRAM_SESSION_TIMEOUT: int = 86400 * 3 # 3 day in seconds
    
    # Web Scraping
    TAVILY_API_KEY: Optional[str] = None
    
    # Credit System Configuration
    DAILY_CREDITS_LIMIT: int = 100  # Total daily credits per user
    CREDIT_COST_MAIN_ASSISTANT: int = 10  # Credits for main assistant (umumiy)
    CREDIT_COST_SOLIQ_ASSISTANT: int = 15  # Credits for soliq specialized assistant
    CREDIT_COST_DEEPRESEARCH: int = 25  # Credits for deep research / agentic RAG
    
    # Payme Payment Configuration
    PAYME_MERCHANT_ID: str = None  # Payme merchant ID
    PAYME_MERCHANT_KEY: str = None  # Payme merchant key for authorization
    
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
       
    # Google Auth
    GOOGLE_CLIENT_ID: Optional[str] = None
    GOOGLE_CLIENT_SECRET: Optional[str] = None
    GOOGLE_REDIRECT_URI: Optional[str] = None
    AUTH_SECRET_KEY: str = "secret-key-change-me"

    model_config = ConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="allow"  # Allow extra fields in the environment
    )

settings = Settings()