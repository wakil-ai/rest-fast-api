from fastapi import APIRouter

from app.assistants.soliq import SoliqAssistant
from app.core.config import settings
from app.db.db_manager import DBManager
from app.models.retrieval_models import (
    MongoFullTextRequest,
    MongoMetadataRequest,
    VectorDBRequest,
)
from app.retrieval.embedding_manager import EmbeddingManager
from app.retrieval.retrieval_service import RetrievalService

router = APIRouter(prefix="/retrieval", tags=["Retrieval"])

retrieval_service = RetrievalService()
db_manager = DBManager()
embedding_manager = EmbeddingManager()


@router.post("/mongo", summary="Full-text search in MongoDB")
async def mongo_fulltext_search(request: MongoFullTextRequest):
    results = db_manager.mongo_handler.search_fulltext(
        collection=settings.COLLECTION_NAME, query_text=request.query_text
    )
    return {"results": results}


@router.post("/mongo-metadata", summary="Metadata search in MongoDB")
async def mongo_metadata_search(request: MongoMetadataRequest):
    results = db_manager.mongo_handler.search_metadata(
        collection=settings.COLLECTION_NAME, filters=request.filters
    )
    return {"results": results}


@router.post("/", summary="Search in Vector DB without specific method in production")
async def vector_db_search(request: VectorDBRequest):
    results = await retrieval_service.retrieve_context(
        query=request.query_text,
        top_k=request.top_k,
        collection_name=request.collection_name,
    )
    return {"results": results}


@router.post("/search-hybrid", summary="Hybrid search in Vector DB")
async def vector_db_hybrid_search(request: VectorDBRequest):
    embedding = embedding_manager.embed_query(request.query_text)
    results = db_manager.search_hybrid(
        dense_vector=embedding,
        text_query=request.query_text,
        top_k=request.top_k,
        collection_name=request.collection_name,
    )
    return {"results": results}


@router.post("/search-dense", summary="Dense search in Vector DB")
async def vector_db_dense_search(request: VectorDBRequest):
    embedding = embedding_manager.embed_query(request.query_text)
    results = db_manager.search_dense(
        dense_vector=embedding,
        top_k=request.top_k,
        collection_name=request.collection_name,
    )
    return {"results": results}


@router.post("/search-sparse", summary="Sparse search in Vector DB")
async def vector_db_sparse_search(request: VectorDBRequest):
    results = db_manager.search_sparse(
        text_query=request.query_text,
        top_k=request.top_k,
        collection_name=request.collection_name,
    )
    return {"results": results}


@router.post("/search-specific", summary="Specific search in Vector DB")
async def vector_db_specific_search(request: VectorDBRequest):
    results = db_manager.search_specific(
        text_query=request.query_text,
        top_k=request.top_k,
        collection_name=request.collection_name,
    )
    return {"results": results}


@router.post("/search/soliq/assistant", summary="Soliq assistant search in Vector DB")
async def vector_db_soliq_assistant_search(request: VectorDBRequest):
    soliq = SoliqAssistant()
    from app.assistants.base import RetrievalConfig

    config = RetrievalConfig(
        top_k=request.top_k,
        collection_name=settings.MILVUS_SOLIQ_ASSISTANT_NAME,
    )
    results = soliq.search(request.query_text, config)
    return {"results": results}
