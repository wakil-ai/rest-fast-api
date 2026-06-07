from functools import lru_cache
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from app.db import DBManager, MongoHandler
    from app.services import (
        ChatHistoryService,
        ChatService,
        Bitrix24Service,
        ClickService,
        FileManager,
        OTPService,
        ProjectService,
        PromoCodeService,
        RateLimitService,
        RedisService,
        ReferralService,
        FingerprintService,
        StorageService,
        SubscriptionStorage,
        TransactionService,
    )


# DB
@lru_cache
def get_db_manager() -> "DBManager":
    from app.db import DBManager

    return DBManager()


@lru_cache
def get_mongo_handler() -> "MongoHandler":
    from app.db import MongoHandler

    return MongoHandler()


# Services
@lru_cache
def get_redis_service() -> "RedisService":
    from app.services import RedisService

    return RedisService()


@lru_cache
def get_chat_history_service() -> "ChatHistoryService":
    from app.services import ChatHistoryService

    return ChatHistoryService()


@lru_cache
def get_bitrix24_service() -> "Bitrix24Service":
    from app.services import Bitrix24Service

    return Bitrix24Service()


@lru_cache
def get_chat_service() -> "ChatService":
    from app.services import ChatService

    return ChatService()


@lru_cache
def get_llm_service_client():
    from app.services.llm_service_client import LlmServiceClient

    return LlmServiceClient()


@lru_cache
def get_storage_service() -> "StorageService":
    from app.services import StorageService

    return StorageService()


@lru_cache
def get_file_manager() -> "FileManager":
    from app.services import FileManager

    return FileManager()


@lru_cache
def get_project_service() -> "ProjectService":
    from app.services.project_service import ProjectService

    return ProjectService()


@lru_cache
def get_project_member_service() -> "ProjectMemberService":
    from app.services.project_member_service import ProjectMemberService

    return ProjectMemberService()


@lru_cache
def get_rate_limit_service() -> "RateLimitService":
    from app.services import RateLimitService

    return RateLimitService()


@lru_cache
def get_referral_service() -> "ReferralService":
    from app.services import ReferralService

    return ReferralService()


@lru_cache
def get_fingerprint_service() -> "FingerprintService":
    from app.services import FingerprintService

    return FingerprintService()


@lru_cache
def get_promo_code_service() -> "PromoCodeService":
    from app.services import PromoCodeService

    return PromoCodeService()


@lru_cache
def get_transaction_service() -> "TransactionService":
    from app.services import TransactionService

    return TransactionService()


@lru_cache
def get_click_service() -> "ClickService":
    from app.services import ClickService

    return ClickService()


@lru_cache
def get_uzum_service() -> "UzumService":
    from app.services import UzumService

    return UzumService()


@lru_cache
def get_subscription_storage() -> "SubscriptionStorage":
    from app.services import SubscriptionStorage

    return SubscriptionStorage()


@lru_cache
def get_otp_service() -> "OTPService":
    from app.services import OTPService

    return OTPService()
