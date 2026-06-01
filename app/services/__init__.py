from app.services.bitrix24_service import Bitrix24Service
from app.services.auth_service import validate_telegram_data
from app.services.chat_history_service import ChatHistoryService
from app.services.chat_service import ChatService
from app.services.file_management import FileManager
from app.services.memory_service import ChatMemoryService
from app.services.ocr_service import OCRService
from app.services.otp_service import OTPService
from app.services.payments import (
    BasePaymentService,
    ClickService,
    TransactionService,
    UzumService,
)
from app.services.project_service import ProjectService
from app.services.promo_code_service import PromoCodeService
from app.services.rate_limit_service import RateLimitService
from app.services.redis_service import RedisService
from app.services.referral_service import ReferralService
from app.services.fingerprint_service import FingerprintService
from app.services.speech_to_text_service import (
    AzureRESTSpeechToTextService,
    AzureSpeechToTextService,
    GoogleSpeechToTextService,
    SpeechToTextService,
    get_speech_to_text_service,
)
from app.services.storage_service import StorageService
from app.services.streaming_speech_to_text import (
    AzureStreamingSTTService,
    GoogleStreamingSTTService,
    StreamingSTTService,
)
from app.services.subscription_storage import SubscriptionStorage

__all__ = [
    # Auth
    "validate_telegram_data",
    # Services
    "Bitrix24Service",
    "ChatHistoryService",
    "ChatService",
    "FileManager",
    "ChatMemoryService",
    "OCRService",
    "OTPService",
    "BasePaymentService",
    "ClickService",
    "TransactionService",
    "UzumService",
    "PromoCodeService",
    "RateLimitService",
    "ReferralService",
    "FingerprintService",
    "RedisService",
    "StorageService",
    # Speech-to-text
    "SpeechToTextService",
    "GoogleSpeechToTextService",
    "AzureSpeechToTextService",
    "AzureRESTSpeechToTextService",
    "get_speech_to_text_service",
    # Streaming STT
    "StreamingSTTService",
    "GoogleStreamingSTTService",
    "AzureStreamingSTTService",
    "SubscriptionStorage",
    "ProjectService",
]
