from fastapi import APIRouter, HTTPException, status, Request
from typing import List

from app.models.chat_history import (
    CreateConversationRequest,
    AddMessageRequest,
    CreateFeedbackRequest,
    ConversationResponse,
    ConversationListResponse,
    FeedbackResponse,
    ChatFeedback
)
from app.services.chat_history_service import ChatHistoryService
from app.core.logger import logger

router = APIRouter(prefix="/history", tags=["Chat History"])
chat_history_service = ChatHistoryService()


@router.post("/conversations", response_model=dict)
async def create_conversation(request: CreateConversationRequest):
    try:
        conversation = chat_history_service.create_conversation(
            session_id=request.session_id,
            title=request.title or "New Chat"
        )
        return {
            "message": "Conversation created successfully",
            "chat_id": conversation.chat_id,
            "session_id": conversation.session_id,
            "title": conversation.title,
            "created_at": conversation.created_at
        }

    except Exception as e:
        logger.error(f"[ChatHistory] Error creating conversation: {str(e)}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to create conversation"
        )


@router.get("/conversations", response_model=ConversationListResponse)
async def list_conversations(session_id: str, limit: int = 50):
    try:
        conversations = chat_history_service.list_conversations(session_id, limit)
        return conversations

    except Exception as e:
        logger.error(f"[ChatHistory] Error listing conversations: {str(e)}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to list conversations"
        )


@router.get("/conversations/{chat_id}", response_model=ConversationResponse)
async def get_conversation(chat_id: str):
    try:
        conversation = chat_history_service.get_conversation(chat_id)

        if not conversation:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Conversation not found"
            )

        return conversation

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"[ChatHistory] Error getting conversation {chat_id}: {str(e)}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to retrieve conversation"
        )


@router.put("/conversations/{chat_id}", response_model=dict)
async def add_message(chat_id: str, request: AddMessageRequest, session_id: str):
    try:
        success = chat_history_service.add_message(
            chat_id,
            session_id,
            request.message
        )

        if not success:
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="Failed to add message"
            )

        return {
            "message": "Message added successfully",
            "chat_id": chat_id,
            "message_id": request.message.message_id
        }

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"[ChatHistory] Error adding message to {chat_id}: {str(e)}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to add message"
        )


@router.delete("/conversations/{chat_id}", response_model=dict)
async def archive_conversation(chat_id: str):
    try:
        success = chat_history_service.archive_conversation(chat_id)

        if not success:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Conversation not found or already archived"
            )

        return {
            "message": "Conversation archived successfully",
            "chat_id": chat_id
        }

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"[ChatHistory] Error archiving conversation {chat_id}: {str(e)}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to archive conversation"
        )


@router.post("/feedback", response_model=FeedbackResponse)
async def submit_feedback(request: CreateFeedbackRequest):
    try:
        if request.feedback_type not in ["positive", "negative"]:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Feedback type must be 'positive' or 'negative'"
            )

        feedback_response = chat_history_service.submit_feedback(
            request.chat_id,
            request.message_id,
            request.feedback_type,
            request.comment
        )

        return feedback_response

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"[ChatHistory] Error submitting feedback: {str(e)}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to submit feedback"
        )


@router.get("/feedback/{chat_id}", response_model=List[ChatFeedback])
async def get_conversation_feedback(chat_id: str):
    try:
        feedback_list = chat_history_service.get_conversation_feedback(chat_id)
        return feedback_list

    except Exception as e:
        logger.error(f"[ChatHistory] Error getting feedback for {chat_id}: {str(e)}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to retrieve feedback"
        )
