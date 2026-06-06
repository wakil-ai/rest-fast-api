# src/api/v2/history/feedbacks.py
from fastapi import APIRouter, status

from src.core.dependencies import get_chat_history_service
from src.models.chat_history import FeedbackCreateRequest
from src.utils.user_management import handle_service_error

router = APIRouter(prefix="/feedback", tags=["Feedback"])

chat_history_service = get_chat_history_service()


@router.post("", status_code=status.HTTP_201_CREATED)
@handle_service_error
async def create_feedback(request: FeedbackCreateRequest):
    result = await chat_history_service.submit_feedback(
        message_id=request.message_id,
        feedback_type=request.feedback_type,
        feedback_content=request.feedback_content,
    )
    return {"info": result, "message": "Feedback submitted"}


@router.get("/{message_id}")
@handle_service_error
async def get_feedback(message_id: str):
    result = await chat_history_service.get_feedback(message_id)
    return {"info": result, "message": "Feedback retrieved"}


@router.delete("/{message_id}", status_code=status.HTTP_200_OK)
@handle_service_error
async def delete_feedback(message_id: str):
    result = await chat_history_service.delete_feedback(message_id)
    return {"info": result, "message": "Feedback deleted"}
