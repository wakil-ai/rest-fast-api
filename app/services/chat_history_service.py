from typing import List, Optional
from datetime import datetime
from bson import ObjectId
import uuid
from pydantic import BaseModel

from app.db.db_manager import DBManager
from app.core.logger import logger
from app.core.config import settings

class ChatHistoryService:
    def __init__(self):
        self.db_manager = DBManager()
        self.users_collection = settings.USERS_COLLECTION
        self.sessions_collection = settings.SESSIONS_COLLECTION
        self.messages_collection = settings.MESSAGES_COLLECTION
        self.feedback_collection = settings.FEEDBACK_COLLECTION
        self._init_collections()

    def _init_collections(self):
        """Initialize necessary database collections."""
        for collection in [self.users_collection, self.sessions_collection, self.messages_collection, self.feedback_collection]:
            self.db_manager.create_collection(collection)

    def _validate_user_id(self, user_id: str) -> None:
        """Validate user ID."""
        if not user_id or not user_id.strip():
            raise ValueError("User ID cannot be empty")

    def _validate_session_id(self, session_id: str) -> None:
        """Validate session ID."""
        if not session_id or not session_id.strip():
            raise ValueError("Session ID cannot be empty")

    def create_user(self, user_id: str, username: Optional[str] = None, 
                    first_name: Optional[str] = None, last_name: Optional[str] = None, 
                    picture: Optional[str] = None, is_lawyer: Optional[bool] = False) -> dict:
        """Create or retrieve an existing user."""
        self._validate_user_id(user_id)
        existing_user = self.db_manager.find_documents(self.users_collection, {"user_id": user_id})

        if existing_user:
            logger.info(f"User with user_id {user_id} already exists.")
            return existing_user[0]

        user = {
            "user_id": user_id,
            "username": username,
            "first_name": first_name,
            "last_name": last_name,
            "picture": picture,
            "is_lawyer": is_lawyer,
            "created_at": datetime.utcnow(),
            "updated_at": datetime.utcnow()
        }

        result_ids = self.db_manager.insert_documents(self.users_collection, [user])
        if not result_ids:
            raise ValueError("Failed to create user")

        user["_id"] = ObjectId(result_ids[0])
        logger.info(f"Created new user with user_id: {user_id}")
        return user

    def create_session(self, user_id: str, session_id: Optional[str] = None, 
                      title: Optional[str] = None, tags: Optional[List[str]] = None) -> dict:
        """Create or retrieve an existing session."""
        self._validate_user_id(user_id)
        session_id = session_id or str(uuid.uuid4())
        self._validate_session_id(session_id)

        existing_session = self.db_manager.find_documents(
            self.sessions_collection, {"user_id": user_id, "session_id": session_id}
        )

        if existing_session:
            logger.info(f"Session with session_id {session_id} already exists for user {user_id}.")
            return existing_session[0]

        session = {
            "user_id": user_id,
            "session_id": session_id,
            "title": title or "New Chat",
            "tags": tags or [],
            "created_at": datetime.utcnow(),
            "updated_at": datetime.utcnow()
        }

        result_ids = self.db_manager.insert_documents(self.sessions_collection, [session])
        if not result_ids:
            raise ValueError("Failed to create session")

        session["_id"] = ObjectId(result_ids[0])
        logger.info(f"Created new session with session_id: {session_id} for user {user_id}")
        return session

    def get_sessions(self, user_id: str, limit: int = 50) -> List[dict]:
        """Retrieve all sessions for a user."""
        self._validate_user_id(user_id)
        sessions = self.db_manager.find_documents(self.sessions_collection, {"user_id": user_id}, limit=limit)
        logger.info(f"Retrieved {len(sessions)} sessions for user {user_id}")
        return sessions
    
    def edit_session(self, session_id: str, title: Optional[str] = None, tags: Optional[List[str]] = None) -> dict:
        """Edit a session's title or tags."""
        self._validate_session_id(session_id)
        update_fields = {}
        if title is not None:
            update_fields["title"] = title
        if tags is not None:
            update_fields["tags"] = tags
        if not update_fields:
            raise ValueError("No fields to update")

        update_fields["updated_at"] = datetime.utcnow()
        updated_count = self.db_manager.update_documents(
            self.sessions_collection, {"session_id": session_id}, {"$set": update_fields}
        )
        if updated_count == 0:
            raise ValueError("Session not found or no changes made")

        session = self.db_manager.find_documents(self.sessions_collection, {"session_id": session_id})
        logger.info(f"Updated session with session_id: {session_id}")
        return session[0] if session else {}
    
    def delete_session(self, user_id: str, session_id: str) -> None:
        """Delete a session and its messages."""
        self._validate_user_id(user_id)
        self._validate_session_id(session_id)

        deleted_count = self.db_manager.delete_documents(
            self.sessions_collection, {"user_id": user_id, "session_id": session_id}
        )
        if deleted_count == 0:
            raise ValueError("Session not found")

        self.db_manager.delete_documents(
            self.messages_collection, {"user_id": user_id, "session_id": session_id}
        )
        logger.info(f"Deleted session with session_id: {session_id} and its messages for user {user_id}")

    def add_message(self, user_id: str, session_id: str, message_id: str, 
                    content: dict, metadata: Optional[dict] = None) -> dict:
        """Add a message to a session."""
        self._validate_user_id(user_id)
        self._validate_session_id(session_id)
        if not message_id or not message_id.strip():
            raise ValueError("Message ID cannot be empty")

        existing_message = self.db_manager.find_documents(
            self.messages_collection, {"user_id": user_id, "session_id": session_id, "message_id": message_id}
        )

        if existing_message:
            logger.info(f"Message with message_id {message_id} already exists in session {session_id} for user {user_id}.")
            return existing_message[0]

        if isinstance(content, BaseModel):
            content = content.model_dump()

        message = {
            "user_id": user_id,
            "session_id": session_id,
            "message_id": message_id,
            "content": content,
            "metadata": metadata or {},
            "created_at": datetime.utcnow(),
            "updated_at": datetime.utcnow()
        }

        result_ids = self.db_manager.insert_documents(self.messages_collection, [message])
        if not result_ids:
            raise ValueError("Failed to add message")

        message["_id"] = ObjectId(result_ids[0])
        self.db_manager.update_documents(
            self.sessions_collection,
            {"user_id": user_id, "session_id": session_id},
            {"$set": {"updated_at": datetime.utcnow()}}
        )
        logger.info(f"Added new message with message_id: {message_id} to session {session_id} for user {user_id}")
        return message

    def get_messages(self, user_id: str, session_id: str, limit: int = 100) -> List[dict]:
        """Retrieve all messages for a session."""
        self._validate_user_id(user_id)
        self._validate_session_id(session_id)
        # Filter to only get actual messages (not feedback) by ensuring content field exists
        query = {
            "user_id": user_id, 
            "session_id": session_id,
            "content": {"$exists": True}
        }
        messages = self.db_manager.find_documents(
            self.messages_collection, query, limit=limit
        )
        logger.info(f"Retrieved {len(messages)} messages from session {session_id} for user {user_id}")
        return messages
    
    def submit_feedback(self, user_id: str, session_id: str, message_id: str,
                        feedback_type: str, comments: Optional[str] = None) -> dict:
        """Submit feedback for a message."""
        self._validate_user_id(user_id)
        self._validate_session_id(session_id)
        
        if not message_id or not message_id.strip():
            raise ValueError("Message ID cannot be empty")

        feedback = {
            "user_id": user_id,
            "session_id": session_id,
            "message_id": message_id,
            "feedback_type": feedback_type,
            "comments": comments or "",
            "created_at": datetime.utcnow(),
            "updated_at": datetime.utcnow()
        }

        result_ids = self.db_manager.insert_documents(self.feedback_collection, [feedback])
        if not result_ids:
            raise ValueError("Failed to submit feedback")

        feedback["_id"] = ObjectId(result_ids[0])
        logger.info(f"Submitted feedback for message_id: {message_id} in session {session_id} for user {user_id}")
        return feedback
    
    def get_feedback(self, user_id: str, session_id: str, message_id: str) -> List[dict]:
        """Retrieve feedback for a specific message."""
        self._validate_user_id(user_id)
        self._validate_session_id(session_id)
        
        if not message_id or not message_id.strip():
            raise ValueError("Message ID cannot be empty")

        feedbacks = self.db_manager.find_documents(
            self.feedback_collection, 
            {"user_id": user_id, "session_id": session_id, "message_id": message_id}
        )
        logger.info(f"Retrieved {len(feedbacks)} feedback entries for message_id: {message_id} in session {session_id} for user {user_id}")
        return feedbacks