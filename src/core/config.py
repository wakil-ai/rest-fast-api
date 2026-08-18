import os
from datetime import datetime

from dotenv import load_dotenv
from pydantic import ConfigDict, Field, field_validator
from pydantic_settings import BaseSettings

# Manually load the .env file from the root project directory
env_path = os.path.abspath(os.path.join(os.path.dirname(__file__), "../..", ".env"))
# Real process variables (GitHub Actions, Docker, Cloud Run, etc.) must win over
# local .env defaults.
load_dotenv(dotenv_path=env_path, override=False)


class Settings(BaseSettings):
    # General
    # App settings
    APP_NAME: str = "WakilAI Chatbot"
    API_PREFIX: str = "/api/v2"
    VERSION: str = "3.0.0"
    DEBUG: bool = False
    # Whether 5xx error responses may include the real exception detail.
    # Deliberately separate from DEBUG (which also flips the CORS wildcard)
    # so verbose 5xx bodies can never be switched on as a side effect of a
    # CORS change. See core.error_handlers.
    EXPOSE_ERROR_DETAIL: bool = False
    HOST_URL: str = "https://backend.wakil.ai"
    TRACING: bool = False  # Reserved for future OpenTelemetry wiring

    ALLOWED_ORIGINS: list[str] = [
        "https://chat.wakil.ai",
        "https://dev-chat.wakil.ai",
        "http://wakil.ai",
    ]  # CORS allowed origins

    # MongoDB
    MONGODB_URI: str | None = None  # Make optional
    COLLECTION_NAME: str | None = None  # Make optional
    MONGODB_DB_NAME: str = "wakilai"
    #: Criminal-case documents DB used by internal excerpt bridge.
    CRIMINAL_CASES_MONGODB_DATABASE: str = "criminal"
    USERS_COLLECTION: str = "users"
    SESSIONS_COLLECTION: str = "sessions"
    MESSAGES_COLLECTION: str = "messages"
    FILES_COLLECTION: str = "files"
    PROJECTS_COLLECTION: str = "projects"
    PROJECT_MEMBERS_COLLECTION: str = "project_members"
    PROJECT_INVITES_COLLECTION: str = "project_invites"
    PROJECT_INVITE_TTL_HOURS: int = 168
    ORGANIZATIONS_COLLECTION: str = "organizations"
    ORGANIZATION_MEMBERS_COLLECTION: str = "organization_members"
    ORGANIZATION_INVITES_COLLECTION: str = "organization_invites"
    WORKFLOW_STATES_COLLECTION: str = "workflow_states"
    TASKS_COLLECTION: str = "tasks"
    ACTIVITY_LOGS_COLLECTION: str = "activity_logs"
    # Not "ai_outputs": only version 1 of a chain is the agent's work — a human
    # edit inserts a further row with source=human. `source` carries that.
    DRAFTS_COLLECTION: str = "drafts"
    ORG_INVITE_TTL_HOURS: int = 168
    # Head + 5 staff members. Defaulted onto each org document as `seat_limit`, so
    # raising the cap for one customer is a data change, not a code change.
    ORG_SEAT_LIMIT: int = 6
    PROMO_CODE_COLLECTION: str = "promos"
    USER_PROMO_CODE_COLLECTION: str = "user-promos"
    TRANSACTION_COLLECTION: str = "transactions"
    SUBSCRIPTIONS_COLLECTION: str = "subscriptions"
    DAILY_SUBSCRIPTIONS_COLLECTION: str = "daily_subscriptions"
    RATE_LIMIT_COLLECTION: str = "creditusage"
    TOKEN_COUNTING_COLLECTION: str = "token_counts"
    TELEGRAM_CHATS_COLLECTION: str = "telegram_chats"
    REFERRAL_SOURCES_COLLECTION: str = "referral_sources"
    FINGERPRINTS_COLLECTION: str = "fingerprints"

    # Google Cloud Storage
    GCS_BUCKET_NAME: str | None = None
    GCS_CREDENTIALS_PATH: str | None = None  # Optional local service account JSON file
    GCS_PROJECT_ID: str | None = None
    GCS_CLIENT_EMAIL: str | None = None
    GCS_PRIVATE_KEY: str | None = None
    GCS_PRIVATE_KEY_ID: str | None = None
    # Shorter than the 60-minute storage default: an avatar is fetched immediately
    # after the JSON response and never re-fetched from the same URL, so a longer
    # window only widens the period in which a leaked URL still works.
    ORG_AVATAR_SIGNED_URL_MINUTES: int = 15
    GCS_CLIENT_ID: str | None = None

    # Assistant compatibility names owned by public routes/credits.
    ASSISTANT_MAIN_COLLECTION: str = "lexuz"
    ASSISTANT_TAX_COLLECTION: str = "soliq"
    ASSISTANT_COURT_COLLECTION: str = "mamuriy_sud_all"
    ASSISTANT_CONTRACT_COLLECTION: str = "shartnoma"
    ASSISTANT_ECONOMIC_COURT_COLLECTION: str = "economic_court"
    ASSISTANT_CIVIL_COURT_COLLECTION: str = "civil_court"
    PROJECT_FILES_INDEX_NAME: str = "project_files"
    LLM_SERVICE_EMBEDDING_MODEL_NAME: str = "rest-api-llm"
    # Min search hit score to offer a contract attachment.
    CONTRACT_ATTACHMENT_MIN_SIMILARITY: float = 0.6
    FILE_CONTENT_TOKEN_LIMIT: int = (
        50_000  # Max uploaded file context tokens for LLM prompts
    )
    FILE_SEARCH_TOP_K: int = 3

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
    DT_TEAM_DISCLAIMER: str = ""

    # OTHERS
    STREAM: bool = True  # Whether to use streaming responses
    # Keep this well below the idle-connection timeout of every proxy/LB in the
    # path (browser -> nuxt -> fast-api -> llm). A long time-to-first-token on a
    # heavy RAG query leaves the stream silent; without a frequent-enough
    # heartbeat an intermediary closes the idle socket and the user sees a bogus
    # "connection lost" even though the answer is still being generated and gets
    # persisted. 15s matches the Nuxt SSE proxy heartbeat.
    STREAM_KEEPALIVE_INTERVAL_SECONDS: float = 15.0
    STREAM_SSE_MAX_RESPONSE_CHARS: int = 200
    TOP_K: int = 10
    CHAT_HISTORY_LIMIT: int = 3
    MAX_QUERY_LENGTH: int = 5000

    # Internal LLM service (rest-api -> rest-api-llm)
    LLM_SERVICE_URL: str | None = None
    LLM_SERVICE_INTERNAL_TOKEN: str | None = None
    LLM_SERVICE_INTERNAL_HEADER: str = "x-internal-token"
    LLM_SERVICE_TIMEOUT_SECONDS: float = 600.0
    # Delegation only. Ordinary chat keeps its unlimited stream: this bound exists
    # so an abandoned draft's slot can be reclaimed once its generation is
    # guaranteed dead, not to make chat stricter.
    AI_DELEGATION_TIMEOUT_SECONDS: int = 900

    # File processing
    DOCUMENT_PROCESSING_POLL_TIMEOUT_SECONDS: int = 900

    # Auth (For Telegram Login)
    TELEGRAM_BOT_TOKEN: str = None
    TELEGRAM_BOT_LOGIN: str = None
    TELEGRAM_SESSION_TIMEOUT: int = 86400 * 3  # 3 day in seconds

    # Credit System Configuration
    DAILY_CREDITS_LIMIT: int = 30  # Daily credits after the welcome pool is exhausted
    SIGNUP_DAY_CREDITS_LIMIT: int = (
        100  # One-time welcome pool for new users (persists until used)
    )
    CREDIT_COST_MAIN_ASSISTANT: int = 10  # Credits for main assistant (umumiy)
    CREDIT_COST_SOLIQ_ASSISTANT: int = 20  # Credits for tax specialized assistant
    CREDIT_COST_SUD_ASSISTANT: int = 25  # Credits for sud specialized assistant
    CREDIT_COST_SHARTNOMA_ASSISTANT: int = 25  # Credits for contract analyzer assistant

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

    # Uzum Merchant API Configuration (webhooks: /check /create /confirm /reverse /status)
    UZUM_USERNAME: str | None = None  # Basic Auth username Uzum will send
    UZUM_PASSWORD: str | None = None  # Basic Auth password Uzum will send
    UZUM_SERVICE_ID: int | None = (
        None  # Single service id assigned to wakil.ai in Uzum catalog
    )
    UZUM_TRANSACTIONS_COLLECTION: str = "uzum_transactions"

    # Apple App Store (StoreKit 2) In-App Purchases
    APPSTORE_BUNDLE_ID: str = "ai.humblebee.wakil"
    # Xcode Debug build's bundle id, trusted only while DEBUG is on.
    # Defaults to "<APPSTORE_BUNDLE_ID>-debug".
    APPSTORE_DEBUG_BUNDLE_ID: str | None = None
    # Numeric App Store app id ("Apple ID" in App Store Connect → App Information).
    # REQUIRED before production JWS verification will work.
    APPSTORE_APP_APPLE_ID: int | None = None
    # OCSP revocation checks during Apple cert-chain verification. Needs outbound
    # network to Apple; disable only if the deploy env can't reach Apple's OCSP.
    APPSTORE_ENABLE_ONLINE_CHECKS: bool = True
    # Directory of Apple root CA .cer/.der files. Defaults to src/resources/certs/apple.
    APPSTORE_ROOT_CERTS_DIR: str | None = None
    APPSTORE_TRANSACTIONS_COLLECTION: str = "appstore_transactions"

    # Payme Subscriptions — daily passes (stack on free quota; credits per pass tier)
    PAYME_SUBSCRIPTION_BASIC_DAILY_PRICE_SUM: int = 15_000
    PAYME_SUBSCRIPTION_STANDARD_DAILY_PRICE_SUM: int = 30_000
    PAYME_SUBSCRIPTION_PREMIUM_DAILY_PRICE_SUM: int = 50_000
    PAYME_SUBSCRIPTION_STANDARD_MONTHLY_PRICE_SUM: int = 300_000
    PAYME_SUBSCRIPTION_STANDARD_YEARLY_PRICE_SUM: int = 3_000_000
    PAYME_SUBSCRIPTION_PRO_MONTHLY_PRICE_SUM: int = 600_000
    PAYME_SUBSCRIPTION_PRO_YEARLY_PRICE_SUM: int = 6_000_000

    # Testing plan
    PAYME_SUBSCRIPTION_TEST_MONTHLY_PRICE_SUM: int = 10_000
    PAYME_SUBSCRIPTION_TEST_YEARLY_PRICE_SUM: int = 20_000

    # Subscription promo campaign — off by default. Flip SUBSCRIPTION_PROMO_ENABLED
    # + set percent/ends_at via env to run a time-boxed discount with no code
    # change or restart-free rollback (see core.subscription_promo). Applies only
    # to the tiers/periods listed below — daily passes are excluded by default.
    SUBSCRIPTION_PROMO_ENABLED: bool = False
    SUBSCRIPTION_PROMO_PERCENT: int = 0
    SUBSCRIPTION_PROMO_STARTS_AT: datetime | None = None
    SUBSCRIPTION_PROMO_ENDS_AT: datetime | None = None
    SUBSCRIPTION_PROMO_TIERS: str = "standard,pro"
    SUBSCRIPTION_PROMO_PERIODS: str = "monthly,yearly"

    @field_validator("SUBSCRIPTION_PROMO_STARTS_AT", "SUBSCRIPTION_PROMO_ENDS_AT", mode="before")
    @classmethod
    def _blank_promo_datetime_to_none(cls, value):
        # An env var present but left blank (`FOO=`) arrives as "", which pydantic
        # won't parse as a datetime on its own — treat it the same as unset.
        if isinstance(value, str) and not value.strip():
            return None
        return value

    # Google Auth
    GOOGLE_CLIENT_ID: str | None = None
    GOOGLE_CLIENT_SECRET: str | None = None
    GOOGLE_REDIRECT_URI: str | None = None
    AUTH_SECRET_KEY: str = "secret-key-change-me"
    JWT_SECRET_KEY: str | None = Field(default=None, min_length=32)
    JWT_ALGORITHM: str = "HS256"
    JWT_ISSUER: str = "wakilai-frontend"
    JWT_AUDIENCE: str = "wakilai-rest-api"
    AUTH_USER_STATUS_CACHE_TTL_SECONDS: int = Field(default=60, ge=1)

    # Twilio Verify (OTP)
    TWILIO_ACCOUNT_SID: str | None = None
    TWILIO_AUTH_TOKEN: str | None = None
    TWILIO_VERIFY_SERVICE_SID: str | None = None
    #: Max OTP send requests per phone within the window.
    OTP_SEND_LIMIT_PER_PHONE: int = 3
    OTP_SEND_WINDOW_SECONDS: int = 3600
    #: Max OTP verify attempts per phone within the window.
    #: Complements Twilio's per-code 5-attempt cap.
    OTP_VERIFY_LIMIT_PER_PHONE: int = 10
    OTP_VERIFY_WINDOW_SECONDS: int = 3600

    REDIS_HOST: str = "localhost"
    REDIS_PORT: int = 6379
    REDIS_EXPIRATION_SECONDS: int = 86400 * 3  # 3 days in seconds
    REDIS_PASSWORD: str | None = None

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
                "collection_name": self.ASSISTANT_MAIN_COLLECTION,
                "credit_cost": self.CREDIT_COST_MAIN_ASSISTANT,
                "description": "General legal assistant",
                "public": True,
            },
            "tax": {
                "name": "tax",
                "collection_name": self.ASSISTANT_TAX_COLLECTION,
                "credit_cost": self.CREDIT_COST_SOLIQ_ASSISTANT,
                "description": "Tax specialized assistant",
                "public": True,
            },
            "court": {
                "name": "court",
                "collection_name": self.ASSISTANT_COURT_COLLECTION,
                "credit_cost": self.CREDIT_COST_SUD_ASSISTANT,
                "description": "Court assistant that automatically routes to the correct court type",
                "public": True,
            },
            "administrative_court": {
                "name": "administrative_court",
                "collection_name": self.ASSISTANT_COURT_COLLECTION,
                "credit_cost": self.CREDIT_COST_SUD_ASSISTANT,
                "description": "Administrative court specialized assistant",
                "public": False,
            },
            "contract_analyzer": {
                "name": "contract_analyzer",
                "collection_name": self.ASSISTANT_CONTRACT_COLLECTION,
                "credit_cost": self.CREDIT_COST_SHARTNOMA_ASSISTANT,
                "description": "Contract analyzer assistant",
                "public": True,
            },
            "criminal_court": {
                "name": "criminal_court",
                "collection_name": self.ASSISTANT_MAIN_COLLECTION,
                "credit_cost": self.CREDIT_COST_SUD_ASSISTANT,
                "description": "Criminal court specialized assistant",
                "public": False,
            },
            "economic_court": {
                "name": "economic_court",
                "collection_name": self.ASSISTANT_ECONOMIC_COURT_COLLECTION,
                "credit_cost": self.CREDIT_COST_SUD_ASSISTANT,
                "description": "Economic court specialized assistant",
                "public": False,
            },
            "civil_court": {
                "name": "civil_court",
                "collection_name": self.ASSISTANT_CIVIL_COURT_COLLECTION,
                "credit_cost": self.CREDIT_COST_SUD_ASSISTANT,
                "description": "Civil court specialized assistant",
                "public": False,
            },
        }


settings = Settings()
