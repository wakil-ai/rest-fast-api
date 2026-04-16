import asyncio
from datetime import datetime

from pydantic import BaseModel

from app.core.config import settings
from app.core.dependencies import get_db_manager
from app.core.exceptions import (
    InvalidInputError,
    MessageNotFoundError,
    SessionNotFoundError,
    UserNotFoundError,
)
from app.core.logger import logger
from app.models.chat_history import SessionStatus, ShareResponse
from app.utils.user_management import clean_for_mongodb, generate_short_id


class ChatHistoryService:
    """Service for managing chat history, sessions, and user data."""

    def __init__(self):
        """
        Initialize ChatHistoryService with dependency injection.
        """
        self.db_manager = get_db_manager()
        self.users_collection = settings.USERS_COLLECTION
        self.sessions_collection = settings.SESSIONS_COLLECTION
        self.messages_collection = settings.MESSAGES_COLLECTION
        self.files_collection = settings.FILES_COLLECTION
        self.token_counting_collection = settings.TOKEN_COUNTING_COLLECTION

        # Create collections and indexes asynchronously on first use
        asyncio.create_task(self._init_collections())

    async def _init_collections(self):
        """Initialize necessary database collections (async)."""
        for collection in [
            self.users_collection,
            self.sessions_collection,
            self.messages_collection,
            self.files_collection,
            self.token_counting_collection,
        ]:
            await self.db_manager.create_collection(collection)

        # Create indexes for better query performance
        await self._create_indexes()

    async def _create_indexes(self):
        """Create necessary indexes for collections (async)."""
        try:
            # Sessions - query by user
            await self.db_manager.mongo_handler.db[
                self.sessions_collection
            ].create_index([("user_id", 1), ("status", 1), ("updated_at", -1)])

            # Messages - query by session
            await self.db_manager.mongo_handler.db[
                self.messages_collection
            ].create_index([("session_id", 1), ("created_at", 1)])

            # Files - query by message, user
            await self.db_manager.mongo_handler.db[self.files_collection].create_index(
                [("message_id", 1)]
            )
            await self.db_manager.mongo_handler.db[self.files_collection].create_index(
                [("user_id", 1)]
            )

            # Token counts - query by session/user
            await self.db_manager.mongo_handler.db[
                self.token_counting_collection
            ].create_index([("session_id", 1), ("created_at", -1)])
            await self.db_manager.mongo_handler.db[
                self.token_counting_collection
            ].create_index([("user_id", 1), ("created_at", -1)])
        except Exception as e:
            logger.warning(f"Error creating indexes: {str(e)}")

    async def upsert_token_stats(
        self,
        message_id: str,
        *,
        user_id: str | None = None,
        session_id: str | None = None,
        model: str | None = None,
        input_token: int | None = None,
        context_token: int | None = None,
        output_token: int | None = None,
        embedding_input_token: int | None = None,
    ) -> None:
        """Store per-message token stats inside message metadata."""
        if not message_id or not message_id.strip():
            return

        message = await self.get_message(message_id)
        if not message:
            logger.warning(
                f"Skipping token stats update because message {message_id} was not found"
            )
            return

        now = datetime.utcnow()

        update: dict = {"updated_at": now}
        if user_id is not None:
            update["user_id"] = user_id
        if session_id is not None:
            update["session_id"] = session_id
        if model is not None:
            update["metadata.model"] = model
        if input_token is not None:
            update["metadata.token_usage.input_token"] = int(input_token)
        if context_token is not None:
            update["metadata.token_usage.context_token"] = int(context_token)
        if output_token is not None:
            update["metadata.token_usage.output_token"] = int(output_token)
        if embedding_input_token is not None:
            update["metadata.token_usage.embedding_input_token"] = int(
                embedding_input_token
            )

        await self.db_manager.update_documents(
            self.messages_collection,
            {"_id": message_id},
            {"$set": update},
        )

    @staticmethod
    def create_message_id() -> str:
        """Create a new chat message identifier."""
        return generate_short_id("msg-", type="uuid7")

    @staticmethod
    def _build_session_activation_update(session: dict, now: datetime) -> dict:
        update_fields: dict = {
            "updated_at": now,
            "status": SessionStatus.active.value,
        }
        if session.get("status") != SessionStatus.active.value and not session.get(
            "activated_at"
        ):
            update_fields["activated_at"] = now
        return update_fields

    async def ensure_session_for_user(self, user_id: str, session_id: str) -> dict:
        """Validate that the provided session exists and belongs to the user."""
        session = await self._ensure_session_exists(session_id)
        if session.get("user_id") != user_id:
            raise InvalidInputError("Session does not belong to the provided user")
        return session

    # Validation and existence checks
    async def _ensure_user_exists(self, user_id: str) -> dict:
        """Ensure user exists in database, raise UserNotFoundError if not."""
        if not user_id or not user_id.strip():
            raise InvalidInputError("User ID cannot be empty")

        user = await self.db_manager.find_documents(
            self.users_collection, {"_id": user_id}
        )
        if not user:
            raise UserNotFoundError(user_id)
        return user[0]

    async def _ensure_session_exists(self, session_id: str) -> dict:
        """Ensure session exists in database, raise SessionNotFoundError if not."""
        if not session_id or not session_id.strip():
            raise InvalidInputError("Session ID cannot be empty")

        session = await self.db_manager.find_documents(
            self.sessions_collection, {"_id": session_id}
        )
        if not session:
            raise SessionNotFoundError(session_id)
        return session[0]

    async def _ensure_message_exists(self, message_id: str) -> dict:
        """Ensure message exists in database, raise MessageNotFoundError if not."""
        if not message_id or not message_id.strip():
            raise InvalidInputError("Message ID cannot be empty")

        message = await self.db_manager.find_documents(
            self.messages_collection, {"_id": message_id}
        )
        if not message:
            raise MessageNotFoundError(message_id)
        return message[0]

    # Users Management
    async def create_user(
        self,
        user_id: str,
        username: str | None = None,
        first_name: str | None = None,
        last_name: str | None = None,
        phone_number: str | None = None,
        picture: str | None = None,
        web_client: str | None = None,
        external_id: str | None = None,
    ) -> dict:
        """Create or retrieve an existing user. Uses user_id as _id."""
        # Check if user exists using _id
        existing_user = await self.db_manager.find_documents(
            self.users_collection, {"_id": user_id}
        )

        if existing_user:
            logger.info(f"User with user_id {user_id} already exists.")
            return existing_user[0]

        user = {
            "_id": user_id,  # Use user_id as _id
            "user_id": user_id,  # Also store user_id in a separate field for easier querying
            "username": username,
            "first_name": first_name,
            "last_name": last_name,
            "picture": picture,
            "phone_number": phone_number,
            "web_client": web_client,
            "external_id": external_id,  # Store external ID for DT integration
            "is_blocked": False,
            "blocked_at": None,
            "blocked_reason": None,
            "unblocked_at": None,
            "created_at": datetime.utcnow(),
            "updated_at": datetime.utcnow(),
        }

        try:
            # Clean data for MongoDB (convert UUIDs, Decimals, etc.)
            user = clean_for_mongodb(user)
            await self.db_manager.insert_documents(self.users_collection, [user])
            logger.info(f"Created new user with user_id: {user_id}")
            return user
        except Exception as e:
            # Handle race condition: another request may have inserted the user
            if "E11000" in str(e) or "duplicate key" in str(e).lower():
                logger.info(
                    f"User {user_id} was created by a concurrent request, returning existing user."
                )
                existing = await self.db_manager.find_documents(
                    self.users_collection, {"_id": user_id}
                )
                if existing:
                    return existing[0]
            raise ValueError(f"Failed to create user: {str(e)}")

    async def get_user(self, user_id: str) -> dict | None:
        """Get user by user_id."""
        users = await self.db_manager.find_documents(
            self.users_collection, {"_id": user_id}
        )
        return users[0] if users else None

    async def get_user_by_external_id(self, external_id: str) -> dict | None:
        """Get user by external_id (for DT integration)."""
        users = await self.db_manager.find_documents(
            self.users_collection, {"external_id": external_id}
        )
        return users[0] if users else None

    async def update_user_info(
        self, user_id: str, field: str, value: str
    ) -> dict | None:
        """Update a user's information (username, first_name, last_name, picture)."""
        await self._ensure_user_exists(user_id)

        if not field or not field.strip():
            raise InvalidInputError("Field cannot be empty")

        update_fields = {
            field: value,
            "updated_at": datetime.utcnow(),
        }

        # Even if modified_count is 0 (e.g. same value), we still return the current document.
        await self.db_manager.update_documents(
            self.users_collection,
            {"_id": user_id},
            {"$set": update_fields},
        )

        return await self.get_user(user_id)

    async def update_user_phone_number(
        self, user_id: str, phone_number: str
    ) -> dict | None:
        """Update a user's phone number and return updated user."""
        await self._ensure_user_exists(user_id)

        phone_number = (phone_number or "").strip()
        if not phone_number:
            raise InvalidInputError("Phone number cannot be empty")

        update_fields = {
            "phone_number": phone_number,
            "updated_at": datetime.utcnow(),
        }

        # Even if modified_count is 0 (e.g. same value), we still return the current document.
        await self.db_manager.update_documents(
            self.users_collection,
            {"_id": user_id},
            {"$set": update_fields},
        )

        return await self.get_user(user_id)

    async def block_user(self, user_id: str, reason: str | None = None) -> dict | None:
        """Block a user so they cannot log in until unblocked.

        If the user does not exist yet, a minimal user document is created.
        """
        if not user_id or not user_id.strip():
            raise InvalidInputError("User ID cannot be empty")

        now = datetime.utcnow()
        reason = (reason or "").strip() or None

        await self.db_manager.update_documents(
            self.users_collection,
            {"_id": user_id},
            {
                "$set": {
                    "is_blocked": True,
                    "blocked_at": now,
                    "blocked_reason": reason,
                    "unblocked_at": None,
                    "updated_at": now,
                },
                "$setOnInsert": {
                    "_id": user_id,
                    "username": None,
                    "first_name": None,
                    "last_name": None,
                    "picture": None,
                    "phone_number": None,
                    "created_at": now,
                },
            },
            upsert=True,
        )

        return await self.get_user(user_id)

    async def unblock_user(self, user_id: str) -> dict | None:
        """Unblock a previously blocked user."""
        await self._ensure_user_exists(user_id)

        now = datetime.utcnow()

        await self.db_manager.update_documents(
            self.users_collection,
            {"_id": user_id},
            {
                "$set": {
                    "is_blocked": False,
                    "unblocked_at": now,
                    "updated_at": now,
                }
            },
        )

        return await self.get_user(user_id)

    async def is_user_blocked(self, user_id: str) -> bool:
        """Return True if user exists and is blocked."""
        user = await self.get_user(user_id)
        return bool(user and user.get("is_blocked"))

    # Sessions Management
    async def create_session(
        self,
        user_id: str,
        title: str | None = None,
        tags: list[str] | None = None,
    ) -> dict:
        """Create or retrieve an existing session. Uses session_id as _id."""

        await self._ensure_user_exists(user_id)

        # Generate session ID using uuid7 with 'ses-' prefix
        session_id = generate_short_id("ses-", type="uuid7")

        # Session model
        session = {
            "_id": session_id,
            "user_id": user_id,
            "session_id": session_id,  # Ensure session_id field is set to match _id
            "title": title or "New Chat",
            "tags": tags or [],
            "status": SessionStatus.draft.value,
            "activated_at": None,
            "created_at": datetime.utcnow(),
            "updated_at": datetime.utcnow(),
        }

        try:
            await self.db_manager.insert_documents(self.sessions_collection, [session])
            logger.info(f"Created new session with session_id: {session_id}")
            return session
        except Exception as e:
            raise InvalidInputError(f"Failed to create session: {str(e)}")

    async def get_sessions(self, user_id: str, limit: int = 50, skip: int = 0) -> list[dict]:
        """Retrieve sessions for a user."""
        await self._ensure_user_exists(user_id)
        
        sessions = await self.db_manager.find_documents(
            self.sessions_collection, {"user_id": user_id, "status": SessionStatus.active.value}, limit=limit, skip=skip
        )
    
        return sessions

    async def get_session(self, session_id: str) -> dict | None:
        """Get session by session_id."""
        if not session_id or not session_id.strip():
            raise InvalidInputError("Session ID cannot be empty")

        sessions = await self.db_manager.find_documents(
            self.sessions_collection, {"_id": session_id}
        )
        return sessions[0] if sessions else None

    async def edit_session(
        self, session_id: str, title: str | None = None, tags: list[str] | None = None
    ) -> dict:
        """Edit a session's title or tags."""

        await self._ensure_session_exists(session_id)
        update_fields = {}
        if title is not None:
            update_fields["title"] = title
        if tags is not None:
            update_fields["tags"] = tags
        if not update_fields:
            raise InvalidInputError("No fields to update")

        update_fields["updated_at"] = datetime.utcnow()
        updated_count = await self.db_manager.update_documents(
            self.sessions_collection,
            {"_id": session_id},
            {"$set": update_fields},
        )
        if updated_count == 0:
            raise InvalidInputError("Session not found or no changes made")

        session = await self.db_manager.find_documents(
            self.sessions_collection, {"_id": session_id}
        )
        logger.info(f"Updated session with session_id: {session_id}")
        return session[0] if session else {}

    async def delete_session(self, session_id: str) -> None:
        """Delete a session and its messages."""

        # Ensure session exists
        await self._ensure_session_exists(session_id)

        # Delete session
        await self.db_manager.delete_documents(
            self.sessions_collection, {"_id": session_id}
        )

        # Delete associated messages
        await self.db_manager.delete_documents(
            self.messages_collection, {"session_id": session_id}
        )

        # Delete associated message files
        await self.db_manager.delete_documents(
            self.files_collection, {"session_id": session_id, "scope": "message"}
        )

        logger.info(f"Deleted session with session_id: {session_id} and its messages")

    # Messages Management
    async def add_message(
        self,
        session_id: str,
        file_ids: list[str] | None,
        content: dict | BaseModel,
        metadata: dict | None = None,
        message_id: str | None = None,
    ) -> dict:
        """Add a message to a session. Uses message_id as _id."""

        session = await self._ensure_session_exists(session_id)

        resolved_message_id = message_id or self.create_message_id()
        user_id = session["user_id"]

        if isinstance(content, BaseModel):
            content = content.model_dump()

        # Check whether file_id is existed
        for file_id in file_ids or []:
            file = await self.db_manager.find_documents(
                self.files_collection, {"_id": file_id}
            )
            if not file:
                raise InvalidInputError(f"File {file_id} not found")

        message = {
            "_id": resolved_message_id,
            "user_id": user_id,  # Auto-populated from session
            "session_id": session_id,
            "message_id": resolved_message_id,  # Ensure message_id field is set to match _id
            "file_ids": file_ids or [],  # Can be empty list for non-file messages
            "content": content,
            "metadata": metadata or {},
            "created_at": datetime.utcnow(),
            "updated_at": datetime.utcnow(),
        }

        try:
            await self.db_manager.insert_documents(self.messages_collection, [message])

            now = datetime.utcnow()
            session_update = self._build_session_activation_update(session, now)

            # Update session timestamp
            await self.db_manager.update_documents(
                self.sessions_collection,
                {"_id": session_id},
                {"$set": session_update},
            )

            return message
        except Exception as e:
            raise InvalidInputError(f"Failed to add message: {str(e)}")

    async def upsert_message(
        self,
        *,
        session_id: str,
        message_id: str,
        user_id: str,
        file_ids: list[str] | None,
        content: dict | BaseModel,
        metadata: dict | None = None,
    ) -> dict:
        """Create or update a message using a pre-generated message ID."""

        session = await self._ensure_session_exists(session_id)
        if session.get("user_id") != user_id:
            raise InvalidInputError("Session does not belong to the provided user")

        if isinstance(content, BaseModel):
            content = content.model_dump()

        for file_id in file_ids or []:
            file = await self.db_manager.find_documents(
                self.files_collection, {"_id": file_id}
            )
            if not file:
                raise InvalidInputError(f"File {file_id} not found")

        now = datetime.utcnow()
        message_document = clean_for_mongodb(
            {
                "message_id": message_id,
                "user_id": user_id,
                "session_id": session_id,
                "file_ids": file_ids or [],
                "content": content,
                "metadata": metadata or {},
                "updated_at": now,
            }
        )

        await self.db_manager.mongo_handler.db[self.messages_collection].update_one(
            {"_id": message_id},
            {
                "$set": message_document,
                "$setOnInsert": {
                    "_id": message_id,
                    "created_at": now,
                },
            },
            upsert=True,
        )

        session_update = self._build_session_activation_update(session, now)
        await self.db_manager.update_documents(
            self.sessions_collection,
            {"_id": session_id},
            {"$set": session_update},
        )

        saved_message = await self.get_message(message_id)
        return saved_message or {
            "_id": message_id,
            **message_document,
            "created_at": now,
        }

    async def get_messages(self, session_id: str, limit: int = 100) -> list[dict]:
        """Retrieve all messages for a session."""
        query = {
            "session_id": session_id,
            "content": {"$exists": True},  # Ensure it's a message
        }

        messages = await self.db_manager.find_documents(
            self.messages_collection, query, limit=limit
        )

        return messages

    async def get_recent_messages(self, session_id: str, limit: int = 5) -> list[dict]:
        """Retrieve the latest messages for a session in chronological order."""
        messages = await self.get_messages(session_id=session_id, limit=max(limit, 1))
        messages.sort(key=lambda msg: msg.get("created_at") or datetime.min)
        if limit <= 0:
            return []
        return messages[-limit:]

    async def get_message(self, message_id: str) -> dict | None:
        """Get message by message_id (direct _id lookup - fastest)."""

        if not message_id or not message_id.strip():
            raise InvalidInputError("Message ID cannot be empty")

        messages = await self.db_manager.find_documents(
            self.messages_collection, {"_id": message_id}
        )
        return messages[0] if messages else None

    async def share_message(
        self, user_id: str, message_id: str, id_length: int = 32
    ) -> dict:
        """Share a message with another user."""

        # Get message to share
        message = await self.get_message(message_id)
        if not message:
            raise MessageNotFoundError(message_id)

        if message.get("shared"):
            share_record = {
                "share_id": message.get("share_id"),
                "message_id": message_id,
                "url": f"share/{message.get('share_id')}",
                "created_at": message.get("shared_at"),
            }
            logger.info(
                f"Message {message_id} is already shared, returning existing share record."
            )
            return share_record

        # Generate share_id with user_id, session_id, message_id hash
        share_id = generate_short_id(prefix="share-", type="hash", length=id_length)
        logger.info(f"Generated share_id {share_id} for message_id {message_id}")

        # Create share record
        share_record = {
            "share_id": share_id,
            "message_id": message_id,
            "url": f"share/{share_id}",
            "created_at": datetime.utcnow(),
        }

        # Insert share record to message with shared flag
        update_result = await self.db_manager.update_documents(
            self.messages_collection,
            {"_id": message_id},
            {
                "$set": {
                    "shared": True,
                    "share_id": share_id,
                    "shared_by": user_id,
                    "shared_at": datetime.utcnow(),
                }
            },
        )
        if update_result == 0:
            raise InvalidInputError("Failed to update message with share information")

        return share_record

    async def get_share(self, share_id: str) -> ShareResponse | None:
        """Get share by share_id."""

        if not share_id or not share_id.strip():
            raise InvalidInputError("Share ID cannot be empty")

        message = await self.db_manager.find_documents(
            self.messages_collection, {"share_id": share_id}
        )
        message = message[0] if message else None
        if not message:
            raise InvalidInputError(f"Share {share_id} not found")

        created_at = message.get("shared_at") or datetime.utcnow()

        return ShareResponse.model_validate(
            {
                "_id": share_id,
                "question": message["content"].get("query"),
                "answer": message["content"].get("response"),
                "assistant": message["metadata"].get("assistant"),
                "created_at": created_at,
            }
        )

    # Feedback Management
    async def submit_feedback(
        self,
        message_id: str,
        feedback_type: str,
        feedback_content: str | None = None,
    ) -> dict:
        """Embed feedback directly on the message document."""

        if not message_id or not message_id.strip():
            raise InvalidInputError("Message ID cannot be empty")

        await self.db_manager.update_documents(
            self.messages_collection,
            {"_id": message_id},
            {
                "$set": {
                    "feedback_type": feedback_type,
                    "feedback_content": feedback_content,
                    "updated_at": datetime.utcnow(),
                }
            },
        )
        logger.info(f"Submitted feedback for message_id: {message_id}")
        return {"message_id": message_id, "feedback_type": feedback_type, "feedback_content": feedback_content}

    async def get_feedback(self, message_id: str) -> dict | None:
        """Retrieve feedback from the message document."""

        if not message_id or not message_id.strip():
            raise InvalidInputError("Message ID cannot be empty")

        message = await self.get_message(message_id)
        if not message:
            return None
        return {
            "message_id": message_id,
            "feedback_type": message.get("feedback_type"),
            "feedback_content": message.get("feedback_content"),
        }

    async def delete_feedback(self, message_id: str) -> dict:
        """Remove feedback fields from the message document."""

        if not message_id or not message_id.strip():
            raise InvalidInputError("Message ID cannot be empty")

        await self.db_manager.update_documents(
            self.messages_collection,
            {"_id": message_id},
            {"$unset": {"feedback_type": "", "feedback_content": ""}},
        )
        logger.info(f"Deleted feedback for message_id: {message_id}")
        return {"message_id": message_id, "deleted": True}

    # File Management
    async def add_file_upload(
        self,
        user_id: str,
        file_id: str,
        file_url: str,
        ocr_result: str,
        file_metadata: dict,
        status: str,
        scope: str,
        message_id: str | None = None,
    ) -> dict:
        """Add a file upload record. Uses file_id as _id."""

        await self._ensure_user_exists(user_id)

        if not file_id or not file_id.strip():
            raise InvalidInputError("File ID cannot be empty")
        if not file_url or not file_url.strip():
            raise InvalidInputError("File URL cannot be empty")
        if not file_metadata or not isinstance(file_metadata, dict):
            raise InvalidInputError("File metadata must be a valid dictionary")
        if scope not in ["message"]:
            raise InvalidInputError("Scope must be 'message'")
        if scope == "message" and not message_id:
            message_id = None  # Allow message_id to be None initially

        # Check if file exists using _id
        existing_file = await self.db_manager.find_documents(
            self.files_collection, {"_id": file_id}
        )

        if existing_file:
            logger.info(f"File with file_id {file_id} already exists.")
            return existing_file[0]

        file_record = {
            "_id": file_id,
            "file_id": file_id,
            "user_id": user_id,
            "message_id": message_id,
            "scope": scope,
            "file_url": file_url,
            "ocr_result": ocr_result,
            "file_metadata": file_metadata,
            "status": status,
            "created_at": datetime.utcnow(),
            "updated_at": datetime.utcnow(),
        }

        try:
            await self.db_manager.insert_documents(self.files_collection, [file_record])
            logger.info(f"Added file upload record with file_id: {file_id}")
            return file_record
        except Exception as e:
            raise InvalidInputError(f"Failed to add file upload record: {str(e)}")

    async def get_files_by_scope(
        self,
        message_ids: list[str] | None = None,
        limit: int = 50,
    ) -> list[dict]:
        """Retrieve files by message scope."""

        if not message_ids:
            raise InvalidInputError("message_ids must be provided")

        query = {
            "message_id": {"$in": message_ids},
            "scope": "message",
        }

        files = await self.db_manager.find_documents(
            self.files_collection, query, limit=limit
        )
        logger.info(f"Retrieved {len(files)} files")
        return files

    async def get_files_by_user(self, user_id: str, limit: int = 50) -> list[dict]:
        """Retrieve all message-scoped files for a user."""

        await self._ensure_user_exists(user_id)

        query = {"user_id": user_id, "scope": "message"}
        files = await self.db_manager.find_documents(
            self.files_collection, query, limit=limit
        )
        logger.info(f"Retrieved {len(files)} message files for user {user_id}")
        return files

    async def get_files_by_message(
        self, message_id: str, limit: int = 50
    ) -> list[dict]:
        """Retrieve all files associated with a specific message."""

        if not message_id or not message_id.strip():
            raise InvalidInputError("Message ID cannot be empty")

        query = {"message_id": message_id}
        files = await self.db_manager.find_documents(
            self.files_collection, query, limit=limit
        )
        logger.info(f"Retrieved {len(files)} files for message {message_id}")
        return files

    async def get_file_by_id(self, file_id: str) -> dict | None:
        """Retrieve a specific file by file_id (direct _id lookup - fastest)."""

        if not file_id or not file_id.strip():
            raise InvalidInputError("File ID cannot be empty")

        files = await self.db_manager.find_documents(
            self.files_collection, {"_id": file_id}
        )

        if files:
            logger.info(f"Retrieved file with file_id: {file_id}")
            return files[0]

        logger.warning(f"File with file_id {file_id} not found")
        return None

    async def update_file_status(
        self, file_id: str, status: str, ocr_result: str | None = None
    ) -> dict:
        """Update file processing status and OCR result."""

        if not file_id or not file_id.strip():
            raise InvalidInputError("File ID cannot be empty")

        update_fields = {"status": status, "updated_at": datetime.utcnow()}
        if ocr_result is not None:
            update_fields["ocr_result"] = ocr_result

        updated_count = await self.db_manager.update_documents(
            self.files_collection,
            {"_id": file_id},
            {"$set": update_fields},
        )

        if updated_count == 0:
            raise InvalidInputError("File not found or no changes made")

        file = await self.get_file_by_id(file_id)
        logger.info(f"Updated file status for file_id: {file_id} to {status}")
        if not file:
            raise InvalidInputError("File not found after update")
        return file

    async def update_file_message_id(self, file_id: str, message_id: str) -> None:
        """Associate a file with a message."""

        if not file_id or not file_id.strip():
            raise InvalidInputError("File ID cannot be empty")
        if not message_id or not message_id.strip():
            raise InvalidInputError("Message ID cannot be empty")

        updated_count = await self.db_manager.update_documents(
            self.files_collection,
            {"_id": file_id},
            {"$set": {"message_id": message_id, "updated_at": datetime.utcnow()}},
        )

        if updated_count == 0:
            raise InvalidInputError("File not found or no changes made")

        logger.info(f"Associated file {file_id} with message {message_id}")

    async def delete_file_upload(self, file_id: str) -> None:
        """Delete a file upload record."""

        if not file_id or not file_id.strip():
            raise InvalidInputError("File ID cannot be empty")

        deleted_count = await self.db_manager.delete_documents(
            self.files_collection, {"_id": file_id}
        )
        if deleted_count == 0:
            raise InvalidInputError("File not found")
