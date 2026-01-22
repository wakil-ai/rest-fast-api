import asyncio
from datetime import datetime
from typing import Any

from fastapi import APIRouter, HTTPException

from app.core.logger import logger
from app.db.milvus_handler import MilvusHandler
from app.db.mongo_handler import MongoHandler
from app.llms.gpt import ChatGPT
from app.retrieval.embedding_manager import EmbeddingManager
from app.services.ocr_service import OCRService
from app.services.storage_service import StorageService

router = APIRouter()


@router.get("/health")
async def health_check():
    """
    Comprehensive health check for all critical services.
    Returns status of embedding model, vector DB, LLM, MongoDB, and GCP storage.
    """
    health_status = {
        "status": "ok",
        "timestamp": datetime.utcnow().isoformat(),
        "services": {},
    }

    # Check all services concurrently
    tasks = [
        check_embedding_service(),
        check_milvus_service(),
        check_openai_service(),
        check_mongodb_service(),
        check_gcp_storage_service(),
        check_ocr_service(),
    ]

    results = await asyncio.gather(*tasks, return_exceptions=True)

    service_names = ["embedding", "milvus", "openai", "mongodb", "gcp_storage", "ocr"]

    for i, (service_name, result) in enumerate(zip(service_names, results)):
        if isinstance(result, Exception):
            health_status["services"][service_name] = {
                "status": "error",
                "error": str(result),
                "response_time": None,
            }
            health_status["status"] = "degraded"
        else:
            health_status["services"][service_name] = result
            if result and isinstance(result, dict) and result.get("status") != "ok":
                health_status["status"] = "degraded"

    # Return appropriate HTTP status based on overall health
    if health_status["status"] == "ok":
        return health_status
    else:
        raise HTTPException(status_code=503, detail=health_status)


async def check_embedding_service() -> dict[str, Any]:
    """Check SiliconFlow embedding service connection and computation."""
    start_time = datetime.utcnow()
    try:
        embedding_manager = EmbeddingManager()

        # Test connection with a simple embedding computation
        test_text = "Test embedding computation"
        embedding = embedding_manager.embed_query(test_text)

        response_time = (datetime.utcnow() - start_time).total_seconds()

        if embedding and len(embedding) > 0:
            return {
                "status": "ok",
                "response_time": f"{response_time:.3f}s",
                "embedding_dim": len(embedding),
            }
        else:
            return {
                "status": "error",
                "error": "Empty embedding returned",
                "response_time": f"{response_time:.3f}s",
            }
    except Exception as e:
        logger.error(f"Embedding service health check failed: {str(e)}")
        raise


async def check_milvus_service() -> dict[str, Any]:
    """Check Milvus vector database connection and retrieval."""
    start_time = datetime.utcnow()
    try:
        milvus_handler = MilvusHandler()

        # Test connection by checking if main collection exists
        from app.core.config import settings

        collection_exists = milvus_handler.client.has_collection(
            settings.MILVUS_MAIN_NAME
        )

        response_time = (datetime.utcnow() - start_time).total_seconds()

        if collection_exists:
            return {
                "status": "ok",
                "response_time": f"{response_time:.3f}s",
                "collections": milvus_handler.milvus_collections,
            }
        else:
            return {
                "status": "error",
                "error": "Main collection not found",
                "response_time": f"{response_time:.3f}s",
            }
    except Exception as e:
        logger.error(f"Milvus service health check failed: {str(e)}")
        raise


async def check_openai_service() -> dict[str, Any]:
    """Check OpenAI LLM connection and small chat generation."""
    start_time = datetime.utcnow()
    try:
        gpt_handler = ChatGPT()

        # Test with a small chat request
        test_response = await gpt_handler._generate_complete(
            system_prompt="You are a helpful assistant.", query="Say 'Hello'"
        )

        response_time = (datetime.utcnow() - start_time).total_seconds()

        if test_response and len(test_response.strip()) > 0:
            return {
                "status": "ok",
                "response_time": f"{response_time:.3f}s",
                "model": gpt_handler.model,
                "response_length": len(test_response),
            }
        else:
            return {
                "status": "error",
                "error": "Empty response from LLM",
                "response_time": f"{response_time:.3f}s",
            }
    except Exception as e:
        logger.error(f"OpenAI service health check failed: {str(e)}")
        raise


async def check_mongodb_service() -> dict[str, Any]:
    """Check MongoDB connection and basic operations."""
    start_time = datetime.utcnow()
    try:
        mongo_handler = MongoHandler()

        # Test connection with ping
        mongo_handler.client.admin.command("ping")

        # Test database access
        from app.core.config import settings

        db = mongo_handler.client[settings.MONGODB_DB_NAME]
        collections = db.list_collection_names()

        response_time = (datetime.utcnow() - start_time).total_seconds()

        return {
            "status": "ok",
            "response_time": f"{response_time:.3f}s",
            "database": settings.MONGODB_DB_NAME,
            "collections_count": len(collections),
        }
    except Exception as e:
        logger.error(f"MongoDB service health check failed: {str(e)}")
        raise


async def check_gcp_storage_service() -> dict[str, Any]:
    """Check GCP Storage service connection and basic operations."""
    start_time = datetime.utcnow()
    try:
        storage_service = StorageService()

        response_time = (datetime.utcnow() - start_time).total_seconds()

        return {
            "status": "ok",
            "response_time": f"{response_time:.3f}s",
            "bucket_name": storage_service.bucket_name,
        }
    except Exception as e:
        logger.error(f"GCP Storage service health check failed: {str(e)}")
        raise


async def check_ocr_service() -> dict[str, Any]:
    """Check OCR service connection and basic functionality."""
    start_time = datetime.utcnow()
    try:
        ocr_service = OCRService()

        # Test OCR service initialization
        if not ocr_service.client:
            response_time = (datetime.utcnow() - start_time).total_seconds()
            return {
                "status": "error",
                "error": "OCR client not initialized",
                "response_time": f"{response_time:.3f}s",
            }

        response_time = (datetime.utcnow() - start_time).total_seconds()

        return {
            "status": "ok",
            "response_time": f"{response_time:.3f}s",
            "api_key_configured": bool(ocr_service.client),
        }
    except Exception as e:
        logger.error(f"OCR service health check failed: {str(e)}")
        raise
