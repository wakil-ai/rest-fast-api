from fastapi import APIRouter, HTTPException, status

from app.core.dependencies import get_chat_history_service
from app.models.chat_history import ShareResponse
from app.utils.user_management import handle_service_error

router = APIRouter(prefix="/share", tags=["Share"])

chat_history_service = get_chat_history_service()


@router.get("/{share_id}", response_model=ShareResponse)
@handle_service_error
async def get_share(share_id: str):
    share = await chat_history_service.get_share(share_id)
    if not share:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Share not found"
        )
    return share
