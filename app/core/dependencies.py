from functools import lru_cache
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from app.orchestration.prompts import PromptRegistry
    from app.orchestration.agents.court_routing import CourtClassifier
    from app.orchestration.agents.criminal_mode_routing import CriminalModeClassifier
    from app.orchestration.agents.intent_recognition import IntentClassifier
    from app.orchestration.agents.milvus_agent import MilvusQueryAgent
    from app.db import DBManager, MilvusHandler, MongoHandler, PineconeHandler
    from app.orchestration.providers import LLM
    from app.orchestration.service import OrchestrationService
    from app.retrieval import (
        EmbeddingManager,
        RetrievalService,
        StandardContextFormatter,
    )
    from app.services import (
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
        StorageService,
        SubscriptionStorage,
        TransactionService,
    )
    from app.services.criminal_case_graph_retrieval import CriminalCaseGraphRetriever


# DB
@lru_cache
def get_db_manager() -> "DBManager":
    from app.db import DBManager

    return DBManager()


@lru_cache
def get_mongo_handler() -> "MongoHandler":
    from app.db import MongoHandler

    return MongoHandler()


@lru_cache
def get_milvus_handler() -> "MilvusHandler":
    from app.db import MilvusHandler

    return MilvusHandler()


@lru_cache
def get_pinecone_handler() -> "PineconeHandler":
    from app.db import PineconeHandler

    return PineconeHandler()


# Retrieval
@lru_cache
def get_embedding_manager() -> "EmbeddingManager":
    from app.retrieval import EmbeddingManager

    return EmbeddingManager()


@lru_cache
def get_context_formatter() -> "StandardContextFormatter":
    from app.retrieval import StandardContextFormatter

    return StandardContextFormatter()


@lru_cache
def get_retrieval_service() -> "RetrievalService":
    from app.retrieval import RetrievalService

    return RetrievalService()


# Chains
@lru_cache
def get_prompt_registry() -> "PromptRegistry":
    from app.orchestration.prompts import PromptRegistry

    return PromptRegistry()


@lru_cache
def get_intent_classifier() -> "IntentClassifier":
    from app.orchestration.agents.intent_recognition import IntentClassifier

    return IntentClassifier()


@lru_cache
def get_court_classifier() -> "CourtClassifier":
    from app.orchestration.agents.court_routing import CourtClassifier

    return CourtClassifier()


@lru_cache
def get_milvus_query_agent() -> "MilvusQueryAgent":
    from app.orchestration.agents.milvus_agent import MilvusQueryAgent

    return MilvusQueryAgent()


@lru_cache
def get_criminal_case_graph_retriever() -> "CriminalCaseGraphRetriever":
    from app.services.criminal_case_graph_retrieval import CriminalCaseGraphRetriever

    return CriminalCaseGraphRetriever()


@lru_cache
def get_criminal_mode_classifier() -> "CriminalModeClassifier":
    from app.orchestration.agents.criminal_mode_routing import CriminalModeClassifier

    return CriminalModeClassifier()


@lru_cache
def get_orchestration_service() -> "OrchestrationService":
    from app.orchestration.service import OrchestrationService

    return OrchestrationService()


@lru_cache
def get_fallback_llm() -> "LLM":
    from app.core.config import settings
    from app.orchestration.providers import ChatGPT, Claude, Gemini, Novita

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
def get_memory_service() -> "ChatMemoryService":
    from app.services import ChatMemoryService

    return ChatMemoryService()


@lru_cache
def get_chat_service() -> "ChatService":
    from app.services import ChatService

    return ChatService()


@lru_cache
def get_storage_service() -> "StorageService":
    from app.services import StorageService

    return StorageService()


@lru_cache
def get_ocr_service() -> "OCRService":
    from app.services import OCRService

    return OCRService()


@lru_cache
def get_file_manager() -> "FileManager":
    from app.services import FileManager

    return FileManager()


@lru_cache
def get_project_service() -> "ProjectService":
    from app.services.project_service import ProjectService

    return ProjectService()


@lru_cache
def get_rate_limit_service() -> "RateLimitService":
    from app.services import RateLimitService

    return RateLimitService()


@lru_cache
def get_referral_service() -> "ReferralService":
    from app.services import ReferralService

    return ReferralService()


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
def get_subscription_storage() -> "SubscriptionStorage":
    from app.services import SubscriptionStorage

    return SubscriptionStorage()


@lru_cache
def get_otp_service() -> "OTPService":
    from app.services import OTPService

    return OTPService()
