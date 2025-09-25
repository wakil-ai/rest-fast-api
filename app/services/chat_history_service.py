from typing import List, Optional
from datetime import datetime
from bson import ObjectId
import uuid
import re

from app.db.db_manager import DBManager
from app.models.chat_history import (
    ChatConversation,
    ChatMessage,
    ChatFeedback,
    ConversationResponse,
    ConversationListResponse,
    ConversationListItem,
    FeedbackResponse
)
from app.core.logger import logger
from app.core.config import settings


class ChatHistoryService:
    def __init__(self):
        self.db_manager = DBManager()
        self.conversations_collection = settings.MONGO_CONVERSATIONS_COLLECTION
        self.feedback_collection = settings.MONGO_FEEDBACK_COLLECTION

    @staticmethod
    def generate_chat_id() -> str:
        return f"chat_{uuid.uuid4().hex[:12]}"

    @staticmethod
    def generate_title_from_message(message: str) -> str:
        if not message or len(message.strip()) == 0:
            return "New Chat"

        clean_message = re.sub(r'[^\w\s\u0400-\u04FF]', '', message.strip())

        if len(clean_message) > 50:
            title = clean_message[:47] + "..."
        else:
            title = clean_message

        return title if title.strip() else "New Chat"

    def create_conversation(self, session_id: str, title: str = "New Chat") -> ChatConversation:
        try:
            chat_id = self.generate_chat_id()
            conversation = ChatConversation(
                chat_id=chat_id,
                session_id=session_id,
                title=title
            )

            conversation_dict = conversation.dict(by_alias=True, exclude={"id"})
            result_ids = self.db_manager.insert_documents(
                collection_name=self.conversations_collection,
                documents=[conversation_dict]
            )

            if result_ids and len(result_ids) > 0:
                conversation.id = ObjectId(result_ids[0])
                logger.info(f"Created conversation {chat_id} for session: {session_id}")
                return conversation
            else:
                raise Exception("Failed to create conversation")

        except Exception as e:
            logger.error(f"Error creating conversation: {str(e)}")
            raise

    def get_conversation(self, chat_id: str) -> Optional[ConversationResponse]:
        try:
            query = {"chat_id": chat_id, "status": "active"}
            conversations = self.db_manager.find_documents(
                collection_name=self.conversations_collection,
                query=query
            )

            if conversations and len(conversations) > 0:
                conv_data = conversations[0]
                return ConversationResponse(
                    chat_id=conv_data["chat_id"],
                    session_id=conv_data["session_id"],
                    title=conv_data.get("title", "New Chat"),
                    messages=[ChatMessage(**msg) for msg in conv_data.get("messages", [])],
                    total_messages=conv_data.get("total_messages", 0),
                    created_at=conv_data["created_at"],
                    updated_at=conv_data["updated_at"]
                )
            return None

        except Exception as e:
            logger.error(f"Error getting conversation {chat_id}: {str(e)}")
            raise

    def list_conversations(self, session_id: str, limit: int = 50) -> ConversationListResponse:
        try:
            query = {"session_id": session_id, "status": "active"}
            conversations = self.db_manager.find_documents(
                collection_name=self.conversations_collection,
                query=query
            )

            conversations.sort(key=lambda x: x.get("updated_at", datetime.min), reverse=True)
            conversations = conversations[:limit]

            conversation_items = []
            for conv in conversations:
                messages = conv.get("messages", [])
                last_message_preview = None
                if messages:
                    last_msg = messages[-1]
                    content = last_msg.get("content", "")
                    last_message_preview = content[:100] + "..." if len(content) > 100 else content

                conversation_items.append(ConversationListItem(
                    chat_id=conv["chat_id"],
                    title=conv.get("title", "New Chat"),
                    created_at=conv["created_at"],
                    updated_at=conv["updated_at"],
                    total_messages=conv.get("total_messages", 0),
                    last_message_preview=last_message_preview
                ))

            return ConversationListResponse(
                conversations=conversation_items,
                total_count=len(conversation_items)
            )

        except Exception as e:
            logger.error(f"Error listing conversations for session {session_id}: {str(e)}")
            raise


    def add_message(self, chat_id: str, session_id: str, message: ChatMessage) -> bool:
        try:
            existing = self.get_conversation(chat_id)
            if not existing:
                title = "New Chat"
                if message.type == "question":
                    title = self.generate_title_from_message(message.content)

                conversation = self.create_conversation(session_id, title)
                chat_id = conversation.chat_id

            query = {"chat_id": chat_id, "status": "active"}
            update = {
                "$push": {"messages": message.dict()},
                "$inc": {"total_messages": 1},
                "$set": {"updated_at": datetime.utcnow()}
            }

            result = self.db_manager.update_documents(
                collection_name=self.conversations_collection,
                query=query,
                update=update
            )

            success = result.modified_count > 0
            if success:
                logger.info(f"Added message to conversation {chat_id}")
            return success

        except Exception as e:
            logger.error(f"Error adding message to conversation {chat_id}: {str(e)}")
            raise

    def submit_feedback(self, chat_id: str, message_id: str, feedback_type: str, comment: str = None) -> FeedbackResponse:
        try:
            feedback = ChatFeedback(
                chat_id=chat_id,
                message_id=message_id,
                feedback_type=feedback_type,
                comment=comment
            )

            query = {"chat_id": chat_id, "message_id": message_id}
            feedback_dict = feedback.dict(by_alias=True, exclude={"id"})
            update = {
                "$set": feedback_dict,
                "$setOnInsert": {}
            }

            result = self.db_manager.update_documents(
                collection_name=self.feedback_collection,
                query=query,
                update=update,
                upsert=True
            )

            action = "updated" if result.modified_count > 0 else "created"
            logger.info(f"Feedback {action} for message {message_id}: {feedback_type}")

            return FeedbackResponse(
                feedback_id=str(result.upserted_id) if result.upserted_id else "updated",
                message=f"Feedback {action} successfully"
            )

        except Exception as e:
            logger.error(f"Error submitting feedback: {str(e)}")
            raise

    def get_conversation_feedback(self, chat_id: str) -> List[ChatFeedback]:
        try:
            query = {"chat_id": chat_id}
            feedback_docs = self.db_manager.find_documents(
                collection_name=self.feedback_collection,
                query=query
            )

            return [ChatFeedback(**doc) for doc in feedback_docs] if feedback_docs else []

        except Exception as e:
            logger.error(f"Error getting feedback for conversation {chat_id}: {str(e)}")
            raise

    def get_message_feedback(self, message_id: str) -> Optional[ChatFeedback]:
        try:
            query = {"message_id": message_id}
            feedback_doc = self.db_manager.find_documents(
                collection_name=self.feedback_collection,
                query=query
            )

            if feedback_doc and len(feedback_doc) > 0:
                return ChatFeedback(**feedback_doc[0])
            return None

        except Exception as e:
            logger.error(f"Error getting feedback for message {message_id}: {str(e)}")
            return None

    def archive_conversation(self, chat_id: str) -> bool:
        try:
            query = {"chat_id": chat_id}
            update = {
                "$set": {
                    "status": "archived",
                    "updated_at": datetime.utcnow()
                }
            }

            result = self.db_manager.update_documents(
                collection_name=self.conversations_collection,
                query=query,
                update=update
            )

            success = result.modified_count > 0
            if success:
                logger.info(f"Archived conversation {chat_id}")
            return success

        except Exception as e:
            logger.error(f"Error archiving conversation {chat_id}: {str(e)}")
            raise