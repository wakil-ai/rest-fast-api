from typing import List, Optional
from datetime import datetime
from bson import ObjectId
import uuid
import re

from app.db.db_manager import DBManager
from app.core.logger import logger
from app.core.config import settings


class ChatHistoryService:
    def __init__(self):
        self.db_manager = DBManager()
        self.users_collection = settings.USERS_COLLECTION
        self.sessions_collection = settings.SESSIONS_COLLECTION
        self.messages_collections = settings.MESSAGES_COLLECTION
        self.init_collections()
        
    def init_collections(self):
        # Initialize necessary collections
        
        self.db_manager.create_collection(self.users_collection)
        self.db_manager.create_collection(self.sessions_collection)
        self.db_manager.create_collection(self.messages_collections)

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
    
    def create_user(self, user_id: str, 
                          username: Optional[str] = None, 
                          first_name: Optional[str] = None, 
                          last_name: Optional[str] = None, 
                          picture: Optional[str] = None) -> dict:
        try:
            existing_user = self.db_manager.find_documents(
                collection_name=self.users_collection,
                query={"user_id": user_id}
            )

            if existing_user and len(existing_user) > 0:
                logger.info(f"User with user_id {user_id} already exists.")
                return existing_user[0]

            user = {
                "user_id": user_id,
                "username": username,
                "first_name": first_name,
                "last_name": last_name,
                "picture": picture,
                "created_at": datetime.utcnow(),
                "updated_at": datetime.utcnow()
            }

            result_ids = self.db_manager.insert_documents(
                collection_name=self.users_collection,
                documents=[user]
            )

            if result_ids and len(result_ids) > 0:
                user["_id"] = ObjectId(result_ids[0])
                logger.info(f"Created new user with user_id: {user_id}")
                return user
            else:
                raise Exception("Failed to create user")
        except Exception as e:
            logger.error(f"Error creating user {user_id}: {str(e)}")
            raise

    def create_session(self, user_id: str, sessions_id: str, 
                             title: Optional[str] = "New chat", 
                             tags: Optional[List[str]] = None) -> dict:
        try:
            if not sessions_id:
                sessions_id = str(uuid.uuid4())

            existing_session = self.db_manager.find_documents(
                collection_name=self.sessions_collection,
                query={"user_id": user_id, "session_id": sessions_id}
            )

            if existing_session and len(existing_session) > 0:
                logger.info(f"Session with session_id {sessions_id} already exists for user {user_id}.")
                return existing_session[0]

            session = {
                "user_id": user_id,
                "session_id": sessions_id,
                "title": title or "New chat",
                "tags": tags or [],
                "created_at": datetime.utcnow(),
                "updated_at": datetime.utcnow()
            }

            result_ids = self.db_manager.insert_documents(
                collection_name=self.sessions_collection,
                documents=[session]
            )

            if result_ids and len(result_ids) > 0:
                session["_id"] = ObjectId(result_ids[0])
                logger.info(f"Created new session with session_id: {sessions_id} for user {user_id}")
                return session
            else:
                raise Exception("Failed to create session")
        except Exception as e:
            logger.error(f"Error creating session {sessions_id} for user {user_id}: {str(e)}")
            raise
        
    def get_sessions(self, user_id: str, limit: int = 50) -> List[dict]:
        try:
            sessions = self.db_manager.find_documents(
                collection_name=self.sessions_collection,
                query={"user_id": user_id},
            )
            logger.info(f"Retrieved {len(sessions)} sessions for user {user_id}")
            
            
            
            return sessions
        except Exception as e:
            logger.error(f"Error retrieving sessions for user {user_id}: {str(e)}")
            raise
    
    def add_message(self, user_id: str, session_id: str, 
                          message_id: str, content: dict, 
                          metadata: Optional[dict] = None) -> dict:
        try:
            existing_message = self.db_manager.find_documents(
                collection_name=self.messages_collections,
                query={"user_id": user_id, "session_id": session_id, "message_id": message_id}
            )

            if existing_message and len(existing_message) > 0:
                logger.info(f"Message with message_id {message_id} already exists in session {session_id} for user {user_id}.")
                return existing_message[0]

            # Turn content into a dict because it is a pydantic model
            if not isinstance(content, dict):
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

            result_ids = self.db_manager.insert_documents(
                collection_name=self.messages_collections,
                documents=[message]
            )

            if result_ids and len(result_ids) > 0:
                message["_id"] = ObjectId(result_ids[0])
                
                # Update session's updated_at timestamp
                self.db_manager.update_documents(
                    collection_name=self.sessions_collection,
                    query={"user_id": user_id, "session_id": session_id},
                    update={"$set": {"updated_at": datetime.utcnow()}}
                )
                
                logger.info(f"Added new message with message_id: {message_id} to session {session_id} for user {user_id}")
                return message
            else:
                raise Exception("Failed to add message")
        except Exception as e:
            logger.error(f"Error adding message {message_id} to session {session_id} for user {user_id}: {str(e)}")
            raise
        
    
    def get_messages(self, user_id: str, session_id: str, limit: int = 100) -> List[dict]:
        try:
            messages = self.db_manager.find_documents(
                collection_name=self.messages_collections,
                query={"user_id": user_id, "session_id": session_id},
            )
            logger.info(f"Retrieved {len(messages)} messages from session {session_id} for user {user_id}")
            return messages
        except Exception as e:
            logger.error(f"Error retrieving messages from session {session_id} for user {user_id}: {str(e)}")
            raise
    