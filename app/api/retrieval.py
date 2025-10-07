# app/api/retrieval.py

from fastapi import APIRouter
from app.core.config import settings
from app.retrieval.retrieval_service import RetrievalService
from app.models.retrieval_models import MongoFullTextRequest, MongoMetadataRequest, VectorDBRequest

router = APIRouter(prefix="/retrieval", tags=["Retrieval"])

retrieval_service = RetrievalService()

@router.post("/mongo", summary="Full-text search in MongoDB")
async def mongo_fulltext_search(request: MongoFullTextRequest):
    results = retrieval_service.search_mongo_fulltext(
        collection=settings.COLLECTION_NAME,
        query_text=request.query_text
    )
    return {"results": results}

@router.post("/mongo-metadata", summary="Metadata search in MongoDB")
async def mongo_metadata_search(request: MongoMetadataRequest):
    results = retrieval_service.search_mongo_metadata(
        collection=settings.COLLECTION_NAME,
        filters=request.filters
    )
    return {"results": results}

@router.post("/", summary="Search in Vector DB without specific method in production")
async def vector_db_search(request: VectorDBRequest):
    results = retrieval_service.retrieve_context(
        query_text=request.query_text,
        top_k=request.top_k,
    )
    return {"results": results}

@router.post("/search-hybrid", summary="Hybrid search in Vector DB")
async def vector_db_hybrid_search(request: VectorDBRequest):
    results = retrieval_service.search_hybrid(
        query_text=request.query_text,
        top_k=request.top_k,
    )
    return {"results": results}

@router.post("/search-dense", summary="Dense search in Vector DB")
async def vector_db_dense_search(request: VectorDBRequest):
    results = retrieval_service.search_dense(
        query_text=request.query_text,
        top_k=request.top_k,
    )
    return {"results": results}

@router.post("/search-sparse", summary="Sparse search in Vector DB")
async def vector_db_sparse_search(request: VectorDBRequest):
    results = retrieval_service.search_sparse(
        query_text=request.query_text,
        top_k=request.top_k,
    )
    return {"results": results}

@router.post("/search-specific", summary="Specific search in Vector DB")
async def vector_db_specific_search(request: VectorDBRequest):
    results = retrieval_service.search_specific(
        query_text=request.query_text,
        top_k=request.top_k,
    )
    return {"results": results}