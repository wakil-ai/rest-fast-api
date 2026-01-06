from fastapi import APIRouter, HTTPException
from app.services.memory_service import ChatMemoryService
from app.core.logger import logger
from app.models.memory import (
    GetAllMemoriesResponse,
    SaveInteractionRequest,
    SaveInteractionResponse,
    UpdateMemoryRequest,
    UpdateMemoryResponse,
)

router = APIRouter(prefix="/memory", tags=["Memory"])


memory_service = ChatMemoryService()


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
        results = await memory_service.get_all_memories(user_id)
        return {"user_id": user_id, **results}
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
        success = await memory_service.delete_all_user_memories(user_id)
        if success:
            return {"message": f"All memories for user {user_id} deleted successfully."}
        else:
            raise HTTPException(status_code=500, detail="Failed to delete memories.")
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
        messages = [
            {"role": "user", "content": pair.query} for pair in request.messages
        ] + [{"role": "assistant", "content": pair.answer} for pair in request.messages]
        result = await memory_service.add_memory(request.user_id, messages)

        return {
            "message": result.get("message", "Memory added successfully on background.")
        }
    except Exception as e:
        logger.error(f"[ChatMemoryService] Error saving memory: {e}")
        return {"message": f"Failed to save memory: {str(e)}"}
    try:
        return await memory_service._save_interaction_to_memory(
            request.user_id, request.query, request.answer
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.delete(
    "/{memory_id}/", summary="Delete a specific memory by ID", response_model=dict
)
async def delete_memory(memory_id: str):
    """
    Delete a specific memory by its ID.
    """
    try:
        success = await memory_service.delete_memory(memory_id)
        if success:
            return {"message": f"Memory {memory_id} deleted successfully."}
        else:
            raise HTTPException(status_code=500, detail="Failed to delete memory.")
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
        updated_memory = await memory_service.update_memory(memory_id, request.text)
        if updated_memory:
            return {
                "message": f"Memory {memory_id} updated successfully.",
                "memory": updated_memory,
            }
        else:
            raise HTTPException(status_code=500, detail="Failed to update memory.")
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
