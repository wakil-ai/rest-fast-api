import os
from enum import Enum

from dotenv import load_dotenv
from pydantic import ConfigDict
from pydantic_settings import BaseSettings

# Manually load the .env file from the root project directory
env_path = os.path.abspath(os.path.join(os.path.dirname(__file__), "../..", ".env"))
load_dotenv(dotenv_path=env_path, override=True)


class VectorDBType(str, Enum):
    pinecone = "pinecone"
    milvus = "milvus"


class LLMProvider(str, Enum):
    novita = "novita"
    openai = "openai"


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
    API_PREFIX: str = "/api/v2"
    VERSION: str = "5.0.0"
    DEBUG: bool = False
    DEVELOPMENT_MODE: bool = False
    HOST_URL: str = "https://backend.wakil.ai"
    TRACING: bool = False  # Enable tracing for agents and crews

    ALLOWED_ORIGINS: list[str] = [
        "https://chat.wakil.ai",
        "https://dev-chat.wakil.ai",
    ]  # CORS allowed origins

    # Memory Service API Key
    MEM0_API_KEY: str = None  # Mem
    MEM0_PROJECT_ID: str = None
    MEM0_ORG_ID: str = None

    # VECTOR DBs
    # Vector Database Configuration
    VECTOR_DB_TYPE: VectorDBType = VectorDBType.milvus

    # MongoDB
    MONGODB_URI: str | None = None  # Make optional
    COLLECTION_NAME: str | None = None  # Make optional
    MONGODB_DB_NAME: str = "wakilai"
    USERS_COLLECTION: str = "users"
    SESSIONS_COLLECTION: str = "sessions"
    MESSAGES_COLLECTION: str = "messages"
    FILES_COLLECTION: str = "files"
    PROJECTS_COLLECTION: str = "projects"
    PROMO_CODE_COLLECTION: str = "promos"
    USER_PROMO_CODE_COLLECTION: str = "user-promos"
    TRANSACTION_COLLECTION: str = "transactions"
    SUBSCRIPTIONS_COLLECTION: str = "subscriptions"
    DAILY_SUBSCRIPTIONS_COLLECTION: str = "daily_subscriptions"
    RATE_LIMIT_COLLECTION: str = "creditusage"
    TOKEN_COUNTING_COLLECTION: str = "token_counts"
    TELEGRAM_CHATS_COLLECTION: str = "telegram_chats"
    REFERRAL_SOURCES_COLLECTION: str = "referral_sources"

    # Google Cloud Storage
    GCS_BUCKET_NAME: str | None = None
    GCS_CREDENTIALS_PATH: str | None = None  # Path to service account JSON file
    GCS_PROJECT_ID: str | None = None

    # Milvus
    MILVUS_MAIN_NAME: str = "lexuz"
    MILVUS_TAX_COLLECTION: str = "soliq"
    MILVUS_PROJECT_FILES: str = "project_files"
    MILVUS_ADMINISTRATIVE_COURT: str = "mamuriy_sud"
    MILVUS_ADMINISTRATIVE_COURT_ALL: str = "mamuriy_sud_all"
    MILVUS_CONTRACT_ANALYZER: str = "shartnoma"
    MILVUS_ECONOMIC_COURT: str = "economic_court"
    MILVUS_CIVIL_COURT: str = "civil_court"
    MILVUS_CRIMINAL_COURT: str = "criminal_court"
    MILVUS_URI: str = "http://localhost:19530"
    MILVUS_USER: str | None = None
    MILVUS_PASSWORD: str | None = None

    # Pinecone
    PINECONE_API_KEY: str | None = None
    PINECONE_ENVIRONMENT: str | None = None
    PINECONE_INDEX_NAME: str | None = None
    NAMESPACE_NAME: str = "lexuz"

    # LLM
    # Model Selection
    LLM_PROVIDER: LLMProvider = LLMProvider.openai

    # OpenAI GPT
    OPENAI_API_KEY: str | None = None
    DEFAULT_CHAT_MODEL: str = (
        "gemini-3-pro-preview"  # Default model for chat completions
    )
    GPT_COMPLETION_MODEL: str = "gpt-4.1"  # Legacy OpenAI default model

    # Anthropic Claude
    ANTHROPIC_API_KEY: str | None = None

    # Novita AI API for Gemma provider
    NOVITA_API_BASE: str = "https://api.novita.ai/v3/openai"
    NOVITA_API_KEY: str | None = None
    NOVITA_TINY_MODEL: str = "openai/gpt-oss-120b"
    NOVITA_MODEL: str = "openai/gpt-oss-120b"

    # Novita Embeddings (OpenAI-compatible)
    NOVITA_EMBEDDING_BASE_URL: str = "https://api.novita.ai/openai"
    NOVITA_EMBEDDING_MODEL: str = "qwen/qwen3-embedding-8b"

    # DeepInfra Embeddings (OpenAI-compatible)
    DEEPINFRA_API_KEY: str | None = None
    DEEPINFRA_EMBEDDING_BASE_URL: str = "https://api.deepinfra.com/v1/openai"
    DEEPINFRA_EMBEDDING_MODEL: str = "Qwen/Qwen3-Embedding-4B"

    # SiliconFlow Embeddings (OpenAI-compatible)
    SILICONFLOW_API_KEY: str | None = None
    SILICONFLOW_EMBEDDING_BASE_URL: str = "https://api.siliconflow.com/v1/embeddings/"
    SILICONFLOW_EMBEDDING_MODEL: str = "Qwen/Qwen3-Embedding-4B"

    # EMBEDDING MODEL
    EMBEDDING_MODEL: EmbeddingModel = EmbeddingModel.qwen
    EMBEDDING_DIM: int = 2560

    SPEECH_TO_TEXT_PROVIDER: SpeechToTextProvider = SpeechToTextProvider.azure
    AZURE_SPEECH_KEY: str | None = None
    AZURE_SPEECH_REGION: str | None = None

    # OCR Service
    DATALAB_API_KEY: str | None = None
    FILE_CONTENT_TOKEN_LIMIT: int = 30_000  # Max tokens for file content extraction
    MAX_RETRIEVAL_DOCS_TOKEN_LIMIT: int = 200_000  # Max tokens for retrieved documents

    # OpenAI Embedding Model
    OPENAI_EMBEDDING_MODEL: str = "text-embedding-ada-002"

    # Qwen with vllm
    QWEN_EMBEDDING_URL: str | None = None
    QWEN_EMBEDDING_MODEL: str | None = None

    # SECURITY
    # Docs User
    DOCS_USER: str = "admin"
    DOCS_PASSWORD: str = "admin"

    # API Key Authentication
    API_KEY_NAME: str = "admin"
    API_KEY: str = "admin"
    SUPER_ADMIN_KEY_NAME: str = "x-super-admin-key"
    SUPER_ADMIN_API_KEY: str = "super-admin"
    DT_API_KEY_NAME: str = "x-dt-team-api-key"
    DT_API_KEY: str | None = None  # DT team dedicated API key for backend access

    # OTHERS
    STREAM: bool = True  # Whether to use streaming responses
    STREAM_KEEPALIVE_INTERVAL_SECONDS: float = 25.0
    TOP_K: int = 10
    ADDITIONAL_TOP_K: int = (
        3  # For multi-collection retrievals (e.g. contract analyzer + main)
    )
    ALPHA: float = 0.8

    # TEMPERATURE
    TEMPERATURE: float = 0.1
    CHAT_HISTORY_LIMIT: int = 5
    OUTPUT_MAX_TOKENS: int = 8192
    MAX_QUERY_LENGTH: int = 5000

    LOCAL_VLLM_BASE_URL: str = "http://localhost:8000"
    LOCAL_VLLM_MODEL: str = "gpt-oss-120b"
    LOCAL_VLLM_API_KEY: str = "sk-no-key-required"

    # Auth (For Telegram Login)
    TELEGRAM_BOT_TOKEN: str = None
    TELEGRAM_BOT_LOGIN: str = None
    TELEGRAM_SESSION_TIMEOUT: int = 86400 * 3  # 3 day in seconds

    # Web Scraping
    TAVILY_API_KEY: str | None = None

    # Credit System Configuration
    DAILY_CREDITS_LIMIT: int = 100  # Total daily credits per user
    CREDIT_COST_MAIN_ASSISTANT: int = 10  # Credits for main assistant (umumiy)
    CREDIT_COST_SOLIQ_ASSISTANT: int = 20  # Credits for tax specialized assistant
    CREDIT_COST_SUD_ASSISTANT: int = 25  # Credits for sud specialized assistant
    CREDIT_COST_SHARTNOMA_ASSISTANT: int = 25  # Credits for contract analyzer assistant
    CREDIT_COST_DEEPRESEARCH: int = 20  # Credits for deep research / agentic RAG

    # Payme Payment Configuration
    PAYME_MERCHANT_ID: str = None  # Payme merchant ID
    PAYME_MERCHANT_KEY: str = None  # Payme merchant key for authorization
    PAYME_PAYMENT_LINK_BASE: str = (
        "https://checkout.paycom.uz/"  # Base URL for payment links
    )
    PAYMENT_INVOICES_COLLECTION: str = "payment_invoices"
    PAYME_INVOICES_COLLECTION: str = (
        "payme_invoices"  # Stores pre-created payment intents/invoices
    )
    PAYME_FISCAL_COLLECTION: str = "payme_fiscal"  # Stores SetFiscalData payloads

    # Payme Fiscal (CheckPerformTransaction detail)
    PAYME_FISCAL_RECEIPT_TITLE: str = "Online Payment"  # Title for fiscal receipt items
    PAYME_FISCAL_IKPU_CODE: str | None = None  # IKPU code for fiscalization
    PAYME_FISCAL_PACKAGE_CODE: str | None = None  # Package code
    PAYME_FISCAL_VAT_PERCENT: int = 15  # VAT percent
    PAYME_FISCAL_RECEIPT_TYPE: int = 0  # Fiscal receipt type

    # Click Payment Configuration (SHOP-API: prepare/complete + payment button)
    CLICK_MERCHANT_ID: int | None = None
    CLICK_SERVICE_ID: int | None = None
    CLICK_SECRET_KEY: str | None = None
    CLICK_MERCHANT_USER_ID: int | None = None
    CLICK_PAYMENT_LINK_BASE: str = "https://my.click.uz/services/pay"
    CLICK_INVOICES_COLLECTION: str = "click_invoices"
    CLICK_TRANSACTIONS_COLLECTION: str = "click_transactions"

    # Payme Subscriptions
    # Daily: pay-per-day plan (300 credits/day)
    PAYME_SUBSCRIPTION_DAILY_PRICE_SUM: int = 15_000
    PAYME_SUBSCRIPTION_STANDARD_MONTHLY_PRICE_SUM: int = 300_000
    PAYME_SUBSCRIPTION_STANDARD_YEARLY_PRICE_SUM: int = 3_000_000
    PAYME_SUBSCRIPTION_PRO_MONTHLY_PRICE_SUM: int = 600_000
    PAYME_SUBSCRIPTION_PRO_YEARLY_PRICE_SUM: int = 6_000_000

    # Testing plan
    PAYME_SUBSCRIPTION_TEST_MONTHLY_PRICE_SUM: int = 10_000
    PAYME_SUBSCRIPTION_TEST_YEARLY_PRICE_SUM: int = 20_000

    # Google Auth
    GOOGLE_CLIENT_ID: str | None = None
    GOOGLE_CLIENT_SECRET: str | None = None
    GOOGLE_REDIRECT_URI: str | None = None
    AUTH_SECRET_KEY: str = "secret-key-change-me"

    GEMINI_API_KEY: str | None = None

    REDIS_HOST: str = "localhost"
    REDIS_PORT: int = 6379
    REDIS_EXPIRATION_SECONDS: int = 86400 * 3  # 3 days in seconds

    # OneID / B2B Integration
    DT_SERVER_IP: str = "87.192.230.47"  # OneID server IP for birdarcha web client
    DT_WEB_CLIENT_NAME: str = "birdarcha"  # Web client name for OneID integration
    WAKILAI_WEB_CLIENT_NAME: str = "wakilai"  # Web client name for regular users

    # Bitrix24 CRM
    BITRIX24_WEBHOOK_URL: str | None = None
    BITRIX24_LEAD_TITLE: str = "Wakil platform"
    BITRIX24_LEAD_SOURCE_ID: str | None = None
    BITRIX24_LEAD_SOURCE_DESCRIPTION: str = "Wakil platforma"
    BITRIX24_LEAD_ASSIGNED_BY_ID: int | None = None
    BITRIX24_TIMEOUT_SECONDS: float = 10.0

    model_config = ConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="allow",  # Allow extra fields in the environment
    )

    def model_post_init(self, __context) -> None:
        # Build assistants using the already-defined variables
        self.ASSISTANTS = {
            "main": {
                "name": "main",
                "collection_name": self.MILVUS_MAIN_NAME,
                "credit_cost": self.CREDIT_COST_MAIN_ASSISTANT,
                "description": "General legal assistant",
                "public": True,
            },
            "tax": {
                "name": "tax",
                "collection_name": self.MILVUS_TAX_COLLECTION,
                "credit_cost": self.CREDIT_COST_SOLIQ_ASSISTANT,
                "description": "Tax specialized assistant",
                "public": True,
            },
            "court": {
                "name": "court",
                "collection_name": self.MILVUS_ADMINISTRATIVE_COURT_ALL,
                "credit_cost": self.CREDIT_COST_SUD_ASSISTANT,
                "description": "Court assistant that automatically routes to the correct court type",
                "public": True,
            },
            "administrative_court": {
                "name": "administrative_court",
                "collection_name": self.MILVUS_ADMINISTRATIVE_COURT_ALL,
                "credit_cost": self.CREDIT_COST_SUD_ASSISTANT,
                "description": "Administrative court specialized assistant",
                "public": False,
            },
            "contract_analyzer": {
                "name": "contract_analyzer",
                "collection_name": self.MILVUS_CONTRACT_ANALYZER,
                "credit_cost": self.CREDIT_COST_SHARTNOMA_ASSISTANT,
                "description": "Contract analyzer assistant",
                "public": True,
            },
            "criminal_court": {
                "name": "criminal_court",
                "collection_name": self.MILVUS_MAIN_NAME,
                "credit_cost": self.CREDIT_COST_SUD_ASSISTANT,
                "description": "Criminal court specialized assistant",
                "public": False,
            },
            "economic_court": {
                "name": "economic_court",
                "collection_name": self.MILVUS_ECONOMIC_COURT,  # untill updated
                "credit_cost": self.CREDIT_COST_SUD_ASSISTANT,
                "description": "Economic court specialized assistant",
                "public": False,
            },
            "civil_court": {
                "name": "civil_court",
                "collection_name": self.MILVUS_CIVIL_COURT,
                "credit_cost": self.CREDIT_COST_SUD_ASSISTANT,
                "description": "Civil court specialized assistant",
                "public": False,
            },
            "deepresearch": {
                "name": "deepresearch",
                "credit_cost": self.CREDIT_COST_DEEPRESEARCH,
                "description": "Deep research assistant with agentic RAG",
                "public": True,
            },
        }


settings = Settings()
