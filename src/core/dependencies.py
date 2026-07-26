from functools import lru_cache
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from db import DBManager, MongoHandler
    from services import (
        AccountArchiveService,
        AppStoreService,
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
        UzumService,
    )
    from services.project_member_service import ProjectMemberService


# DB
@lru_cache
def get_db_manager() -> "DBManager":
    from db import DBManager

    return DBManager()


@lru_cache
def get_mongo_handler() -> "MongoHandler":
    from db import MongoHandler

    return MongoHandler()


# Services
@lru_cache
def get_redis_service() -> "RedisService":
    from services import RedisService

    return RedisService()


@lru_cache
def get_chat_history_service() -> "ChatHistoryService":
    from services import ChatHistoryService

    return ChatHistoryService()


@lru_cache
def get_account_archive_service() -> "AccountArchiveService":
    from services import AccountArchiveService

    return AccountArchiveService()


@lru_cache
def get_bitrix24_service() -> "Bitrix24Service":
    from services import Bitrix24Service

    return Bitrix24Service()


@lru_cache
def get_chat_service() -> "ChatService":
    from services import ChatService

    return ChatService()


@lru_cache
def get_llm_service_client():
    from services.llm_service_client import LlmServiceClient

    return LlmServiceClient()


@lru_cache
def get_storage_service() -> "StorageService":
    from services import StorageService

    return StorageService()


@lru_cache
def get_file_manager() -> "FileManager":
    from services import FileManager

    return FileManager()


@lru_cache
def get_project_service() -> "ProjectService":
    from services.project_service import ProjectService

    return ProjectService()


@lru_cache
def get_project_member_service() -> "ProjectMemberService":
    from services.project_member_service import ProjectMemberService

    return ProjectMemberService()


@lru_cache
def get_rate_limit_service() -> "RateLimitService":
    from services import RateLimitService

    return RateLimitService()


@lru_cache
def get_referral_service() -> "ReferralService":
    from services import ReferralService

    return ReferralService()


@lru_cache
def get_fingerprint_service() -> "FingerprintService":
    from services import FingerprintService

    return FingerprintService()


@lru_cache
def get_promo_code_service() -> "PromoCodeService":
    from services import PromoCodeService

    return PromoCodeService()


@lru_cache
def get_transaction_service() -> "TransactionService":
    from services import TransactionService

    return TransactionService()


@lru_cache
def get_click_service() -> "ClickService":
    from services import ClickService

    return ClickService()


@lru_cache
def get_uzum_service() -> "UzumService":
    from services import UzumService

    return UzumService()


@lru_cache
def get_appstore_service() -> "AppStoreService":
    from services import AppStoreService

    return AppStoreService()


@lru_cache
def get_subscription_storage() -> "SubscriptionStorage":
    from services import SubscriptionStorage

    return SubscriptionStorage()


@lru_cache
def get_otp_service() -> "OTPService":
    from services import OTPService

    return OTPService()
