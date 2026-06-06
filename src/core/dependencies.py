from functools import lru_cache
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from src.orchestration.prompts import PromptRegistry
    from src.orchestration.agents.court_routing import CourtClassifier
    from src.orchestration.agents.criminal_mode_routing import CriminalModeClassifier
    from src.orchestration.agents.intent_recognition import IntentClassifier
    from src.orchestration.agents.milvus_agent import MilvusQueryAgent
    from src.db import DBManager, MilvusHandler, MongoHandler, PineconeHandler
    from src.orchestration.providers import LLM
    from src.orchestration.service import OrchestrationService
    from src.retrieval import (
        EmbeddingManager,
        RetrievalService,
        StandardContextFormatter,
    )
    from src.services import (
        ChatHistoryService,
        ChatMemoryService,
        ChatService,
        Bitrix24Service,
        ClickService,
        FileManager,
        OCRService,
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
    from src.services.criminal_case_graph_retrieval import CriminalCaseGraphRetriever


# DB
@lru_cache
def get_db_manager() -> "DBManager":
    from src.db import DBManager

    return DBManager()


@lru_cache
def get_mongo_handler() -> "MongoHandler":
    from src.db import MongoHandler

    return MongoHandler()


@lru_cache
def get_milvus_handler() -> "MilvusHandler":
    from src.db import MilvusHandler

    return MilvusHandler()


@lru_cache
def get_pinecone_handler() -> "PineconeHandler":
    from src.db import PineconeHandler

    return PineconeHandler()


# Retrieval
@lru_cache
def get_embedding_manager() -> "EmbeddingManager":
    from src.retrieval import EmbeddingManager

    return EmbeddingManager()


@lru_cache
def get_context_formatter() -> "StandardContextFormatter":
    from src.retrieval import StandardContextFormatter

    return StandardContextFormatter()


@lru_cache
def get_retrieval_service() -> "RetrievalService":
    from src.retrieval import RetrievalService

    return RetrievalService()


# Chains
@lru_cache
def get_prompt_registry() -> "PromptRegistry":
    from src.orchestration.prompts import PromptRegistry

    return PromptRegistry()


@lru_cache
def get_intent_classifier() -> "IntentClassifier":
    from src.orchestration.agents.intent_recognition import IntentClassifier

    return IntentClassifier()


@lru_cache
def get_court_classifier() -> "CourtClassifier":
    from src.orchestration.agents.court_routing import CourtClassifier

    return CourtClassifier()


@lru_cache
def get_milvus_query_agent() -> "MilvusQueryAgent":
    from src.orchestration.agents.milvus_agent import MilvusQueryAgent

    return MilvusQueryAgent()


@lru_cache
def get_criminal_case_graph_retriever() -> "CriminalCaseGraphRetriever":
    from src.services.criminal_case_graph_retrieval import CriminalCaseGraphRetriever

    return CriminalCaseGraphRetriever()


@lru_cache
def get_criminal_mode_classifier() -> "CriminalModeClassifier":
    from src.orchestration.agents.criminal_mode_routing import CriminalModeClassifier

    return CriminalModeClassifier()


@lru_cache
def get_orchestration_service() -> "OrchestrationService":
    from src.orchestration.service import OrchestrationService

    return OrchestrationService()


@lru_cache
def get_fallback_llm() -> "LLM":
    from src.core.config import settings
    from src.orchestration.providers import ChatGPT, Claude, Gemini, Novita

    model_name = settings.DEFAULT_CHAT_MODEL
    mapping = {
        "gemma-": Novita,
        "gpt-oss-": Novita,
        "gpt-": ChatGPT,
        "claude-": Claude,
        "gemini-": Gemini,
    }

    for prefix, cls in mapping.items():
        if model_name.startswith(prefix):
            return cls(model_name=model_name)

    return ChatGPT(model_name=settings.GPT_COMPLETION_MODEL)


# Services
@lru_cache
def get_redis_service() -> "RedisService":
    from src.services import RedisService

    return RedisService()


@lru_cache
def get_chat_history_service() -> "ChatHistoryService":
    from src.services import ChatHistoryService

    return ChatHistoryService()


@lru_cache
def get_bitrix24_service() -> "Bitrix24Service":
    from src.services import Bitrix24Service

    return Bitrix24Service()


@lru_cache
def get_memory_service() -> "ChatMemoryService":
    from src.services import ChatMemoryService

    return ChatMemoryService()


@lru_cache
def get_chat_service() -> "ChatService":
    from src.services import ChatService

    return ChatService()


@lru_cache
def get_storage_service() -> "StorageService":
    from src.services import StorageService

    return StorageService()


@lru_cache
def get_ocr_service() -> "OCRService":
    from src.services import OCRService

    return OCRService()


@lru_cache
def get_file_manager() -> "FileManager":
    from src.services import FileManager

    return FileManager()


@lru_cache
def get_project_service() -> "ProjectService":
    from src.services.project_service import ProjectService

    return ProjectService()


@lru_cache
def get_project_member_service() -> "ProjectMemberService":
    from src.services.project_member_service import ProjectMemberService

    return ProjectMemberService()


@lru_cache
def get_rate_limit_service() -> "RateLimitService":
    from src.services import RateLimitService

    return RateLimitService()


@lru_cache
def get_referral_service() -> "ReferralService":
    from src.services import ReferralService

    return ReferralService()


@lru_cache
def get_fingerprint_service() -> "FingerprintService":
    from src.services import FingerprintService

    return FingerprintService()


@lru_cache
def get_promo_code_service() -> "PromoCodeService":
    from src.services import PromoCodeService

    return PromoCodeService()


@lru_cache
def get_transaction_service() -> "TransactionService":
    from src.services import TransactionService

    return TransactionService()


@lru_cache
def get_click_service() -> "ClickService":
    from src.services import ClickService

    return ClickService()


@lru_cache
def get_uzum_service() -> "UzumService":
    from src.services import UzumService

    return UzumService()


@lru_cache
def get_subscription_storage() -> "SubscriptionStorage":
    from src.services import SubscriptionStorage

    return SubscriptionStorage()


@lru_cache
def get_otp_service() -> "OTPService":
    from src.services import OTPService

    return OTPService()
