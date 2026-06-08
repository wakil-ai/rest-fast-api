from fastapi import APIRouter, HTTPException

from core.dependencies import get_llm_service_client
from core.logger import logger
from models.memory import (
    GetAllMemoriesResponse,
    SaveInteractionRequest,
    SaveInteractionResponse,
    UpdateMemoryRequest,
    UpdateMemoryResponse,
)

router = APIRouter(prefix="/memory", tags=["Memory"])


@router.get(
    "/user/{user_id}/",
    summary="Get all user memories",
    response_model=GetAllMemoriesResponse,
)
async def get_user_memories(user_id: str) -> GetAllMemoriesResponse:
    """
    Retrieve past interactions for a user.
    """
    try:
        return await get_llm_service_client().memory_get_all(user_id)
    except Exception as e:
        logger.error(f"Error retrieving memories for user {user_id}: {e}")
        return {"user_id": user_id, "memories": [], "categories": [], "memory_ids": []}


@router.delete(
    "/user/{user_id}/", summary="Delete all user memories", response_model=dict
)
async def delete_user_memories(user_id: str) -> dict:
    """
    Delete all past interactions for a user.
    """
    try:
        return await get_llm_service_client().request_json(
            "DELETE", f"/api/v1/memory/user/{user_id}/"
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post(
    "/save/",
    summary="Save interaction to memory",
    response_model=SaveInteractionResponse,
)
async def save_interaction(request: SaveInteractionRequest) -> SaveInteractionResponse:
    """
    Save a user interaction (query and answer) to memory.
    """
    try:
        return await get_llm_service_client().post_json(
            "/api/v1/memory/save/",
            request.model_dump(),
        )
    except Exception as e:
        logger.error(f"[ChatMemoryService] Error saving memory: {e}")
        return {"message": f"Failed to save memory: {str(e)}"}


@router.delete(
    "/{memory_id}/", summary="Delete a specific memory by ID", response_model=dict
)
async def delete_memory(memory_id: str):
    """
    Delete a specific memory by its ID.
    """
    try:
        return await get_llm_service_client().request_json(
            "DELETE", f"/api/v1/memory/{memory_id}/"
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.put(
    "/update/{memory_id}",
    summary="Update a specific memory by ID",
    response_model=UpdateMemoryResponse,
)
async def update_memory(
    request: UpdateMemoryRequest, memory_id: str
) -> UpdateMemoryResponse:
    """
    Update a specific memory by its ID.
    """
    try:
        return await get_llm_service_client().request_json(
            "PUT",
            f"/api/v1/memory/update/{memory_id}",
            payload=request.model_dump(),
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
