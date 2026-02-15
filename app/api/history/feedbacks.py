# app/routers/history/feedbacks.py
from fastapi import APIRouter, status

from app.models.chat_history import FeedbackCreateRequest, FeedbackCreateResponse
from app.services.chat_history_service import ChatHistoryService
from app.utils.user_management import handle_service_error, serialize_mongo_id

router = APIRouter(prefix="/feedback", tags=["Feedback"])

chat_history_service = ChatHistoryService()


@router.post("", status_code=status.HTTP_201_CREATED, response_model=FeedbackCreateResponse)
@handle_service_error
async def create_feedback(request: FeedbackCreateRequest):
    fb = await chat_history_service.submit_feedback(
        message_id=request.message_id,
        feedback_type=request.feedback_type,
        comments=request.comments,
    )
    return {"info": serialize_mongo_id(fb), "message": "Feedback submitted"}


@router.get("/{message_id}")
@handle_service_error
async def get_feedback(message_id: str):
    fb = await chat_history_service.get_feedback(message_id)
    return {"info": serialize_mongo_id(fb), "message": "Feedback retrieved"}