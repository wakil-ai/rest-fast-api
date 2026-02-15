from datetime import datetime

from bson import ObjectId
from pydantic import BaseModel

from app.core.config import settings
from app.core.logger import logger
from app.db.db_manager import DBManager
from app.utils.user_management import generate_short_id


class ChatHistoryService:
    # Prevent multiple instances
    _instance = None
    _initialized = False

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
        return cls._instance

    def __init__(self):
        if self._initialized:
            return
        self._initialized = True
        self.db_manager = DBManager()
        self.users_collection = settings.USERS_COLLECTION
        self.sessions_collection = settings.SESSIONS_COLLECTION
        self.messages_collection = settings.MESSAGES_COLLECTION
        self.feedback_collection = settings.FEEDBACK_COLLECTION
        self.files_collection = settings.FILES_COLLECTION
        self.projects_collection = settings.PROJECTS_COLLECTION
        self._init_collections()

    def _init_collections(self):
        """Initialize necessary database collections."""
        for collection in [
            self.users_collection,
            self.projects_collection,
            self.sessions_collection,
            self.messages_collection,
            self.feedback_collection,
            self.files_collection,
        ]:
            self.db_manager.create_collection(collection)

        # Create indexes for better query performance
        self._create_indexes()

    def _create_indexes(self):
        """Create necessary indexes for collections."""
        try:
            # Sessions - query by user and project
            self.db_manager.mongo_handler.db[self.sessions_collection].create_index(
                [("user_id", 1), ("updated_at", -1)]
            )
            self.db_manager.mongo_handler.db[self.sessions_collection].create_index(
                [("project_id", 1)]
            )

            # Messages - query by session
            self.db_manager.mongo_handler.db[self.messages_collection].create_index(
                [("session_id", 1), ("created_at", 1)]
            )

            # Feedback - query by message
            self.db_manager.mongo_handler.db[self.feedback_collection].create_index(
                [("message_id", 1)]
            )

            # Files - query by project, message, user
            self.db_manager.mongo_handler.db[self.files_collection].create_index(
                [("project_id", 1)]
            )
            self.db_manager.mongo_handler.db[self.files_collection].create_index(
                [("message_id", 1)]
            )
            self.db_manager.mongo_handler.db[self.files_collection].create_index(
                [("user_id", 1)]
            )

            # Projects - query by user
            self.db_manager.mongo_handler.db[self.projects_collection].create_index(
                [("user_id", 1)]
            )

            logger.info("[ChatHistoryService] Successfully created database indexes")
        except Exception as e:
            logger.warning(f"Error creating indexes: {str(e)}")

    def _validate_user_id(self, user_id: str) -> None:
        """Validate user ID."""
        if not user_id or not user_id.strip():
            raise ValueError("User ID cannot be empty")

    def _validate_session_id(self, session_id: str) -> None:
        """Validate session ID."""
        if not session_id or not session_id.strip():
            raise ValueError("Session ID cannot be empty")

    def _validate_project_id(self, project_id: str) -> None:
        """Validate project ID."""
        if not project_id or not project_id.strip():
            raise ValueError("Project ID cannot be empty")

    # USER MANAGEMENT
    def create_user(
        self,
        user_id: str,
        username: str | None = None,
        first_name: str | None = None,
        last_name: str | None = None,
        picture: str | None = None,
    ) -> dict:
        """Create or retrieve an existing user. Uses user_id as _id."""
        self._validate_user_id(user_id)

        # Check if user exists using _id
        existing_user = self.db_manager.find_documents(
            self.users_collection, {"_id": user_id}
        )

        if existing_user:
            logger.info(f"User with user_id {user_id} already exists.")
            return existing_user[0]

        user = {
            "_id": user_id,  # Use user_id as _id
            "username": username,
            "first_name": first_name,
            "last_name": last_name,
            "picture": picture,
            "created_at": datetime.utcnow(),
            "updated_at": datetime.utcnow(),
        }

        try:
            self.db_manager.insert_documents(self.users_collection, [user])
            logger.info(f"Created new user with user_id: {user_id}")
            return user
        except Exception as e:
            # Handle race condition: another request may have inserted the user
            if "E11000" in str(e) or "duplicate key" in str(e).lower():
                logger.info(f"User {user_id} was created by a concurrent request, returning existing user.")
                existing = self.db_manager.find_documents(
                    self.users_collection, {"_id": user_id}
                )
                if existing:
                    return existing[0]
            raise ValueError(f"Failed to create user: {str(e)}")

    def get_user(self, user_id: str) -> dict | None:
        """Get user by user_id."""
        self._validate_user_id(user_id)
        users = self.db_manager.find_documents(self.users_collection, {"_id": user_id})
        return users[0] if users else None

    # PROJECT MANAGEMENT
    def create_project(
        self,
        user_id: str,
        project_id: str | None = None,
        title: str | None = None,
    ) -> dict:
        """Create a new project. Uses project_id as _id."""
        self._validate_user_id(user_id)
        project_id = project_id or generate_short_id("proj-")
        self._validate_project_id(project_id)

        # Check if project exists using _id
        existing_project = self.db_manager.find_documents(
            self.projects_collection, {"_id": project_id}
        )

        if existing_project:
            # Verify ownership
            if existing_project[0].get("user_id") != user_id:
                raise ValueError("Project exists but belongs to another user")
            logger.info(f"Project with project_id {project_id} already exists.")
            return existing_project[0]

        project = {
            "_id": project_id,
            "project_id": project_id,  # Ensure project_id field is set to match _id
            "user_id": user_id,
            "title": title or "New Project",
            "created_at": datetime.utcnow(),
            "updated_at": datetime.utcnow(),
        }

        try:
            self.db_manager.insert_documents(self.projects_collection, [project])
            logger.info(f"Created new project with project_id: {project_id}")
            return project
        except Exception as e:
            raise ValueError(f"Failed to create project: {str(e)}")

    def get_projects(self, user_id: str, limit: int = 50) -> list[dict]:
        """Retrieve all projects for a user."""
        self._validate_user_id(user_id)
        projects = self.db_manager.find_documents(
            self.projects_collection, {"user_id": user_id}, limit=limit
        )
        logger.info(f"Retrieved {len(projects)} projects for user {user_id}")
        return projects

    def get_project(self, project_id: str) -> dict | None:
        """Get project by project_id."""
        self._validate_project_id(project_id)
        projects = self.db_manager.find_documents(
            self.projects_collection, {"_id": project_id}
        )
        return projects[0] if projects else None

    def edit_project(self, project_id: str, title: str) -> dict:
        """Edit a project's title."""
        self._validate_project_id(project_id)

        update_fields = {"title": title, "updated_at": datetime.utcnow()}

        updated_count = self.db_manager.update_documents(
            self.projects_collection,
            {"_id": project_id},
            {"$set": update_fields},
        )
        if updated_count == 0:
            raise ValueError("Project not found or no changes made")

        project = self.db_manager.find_documents(
            self.projects_collection, {"_id": project_id}
        )
        logger.info(f"Updated project with project_id: {project_id}")
        return project[0] if project else {}

    def delete_project(self, user_id: str, project_id: str) -> None:
        """Delete a project and all its associated data."""
        self._validate_user_id(user_id)
        self._validate_project_id(project_id)

        # Verify ownership before deletion
        project = self.get_project(project_id)
        if not project:
            raise ValueError("Project not found")
        if project.get("user_id") != user_id:
            raise ValueError("User does not own this project")

        # Delete project
        deleted_count = self.db_manager.delete_documents(
            self.projects_collection, {"_id": project_id}
        )
        if deleted_count == 0:
            raise ValueError("Project not found")

        # Delete associated sessions
        self.db_manager.delete_documents(
            self.sessions_collection, {"project_id": project_id}
        )

        # Delete associated messages (find sessions first, then delete messages)
        sessions = self.db_manager.find_documents(
            self.sessions_collection, {"project_id": project_id}
        )
        for session in sessions:
            self.db_manager.delete_documents(
                self.messages_collection, {"session_id": session["_id"]}
            )

        # Delete associated files
        self.db_manager.delete_documents(
            self.files_collection, {"project_id": project_id}
        )

        logger.info(
            f"Deleted project {project_id} and all associated data for user {user_id}"
        )

    # SESSION MANAGEMENT
    def create_session(
        self,
        user_id: str,
        session_id: str | None = None,
        project_id: str | None = None,
        title: str | None = None,
        tags: list[str] | None = None,
    ) -> dict:
        """Create or retrieve an existing session. Uses session_id as _id."""
        self._validate_user_id(user_id)
        session_id = session_id or generate_short_id("ses-")
        self._validate_session_id(session_id)

        # If project_id provided, validate it exists and user owns it
        if project_id:
            self._validate_project_id(project_id)
            project = self.get_project(project_id)
            if not project:
                raise ValueError(f"Project {project_id} not found")
            if project.get("user_id") != user_id:
                raise ValueError(f"User does not own project {project_id}")

        # Check if session exists using _id
        existing_session = self.db_manager.find_documents(
            self.sessions_collection, {"_id": session_id}
        )

        if existing_session:
            # Verify ownership
            if existing_session[0].get("user_id") != user_id:
                raise ValueError("Session exists but belongs to another user")
            logger.info(f"Session with session_id {session_id} already exists.")
            return existing_session[0]

        session = {
            "_id": session_id,
            "session_id": session_id,  # Ensure session_id field is set to match _id
            "user_id": user_id,
            "project_id": project_id,  # Can be None for standalone chats
            "title": title or "New Chat",
            "tags": tags or [],
            "created_at": datetime.utcnow(),
            "updated_at": datetime.utcnow(),
        }

        try:
            self.db_manager.insert_documents(self.sessions_collection, [session])
            logger.info(f"Created new session with session_id: {session_id}")
            return session
        except Exception as e:
            raise ValueError(f"Failed to create session: {str(e)}")

    def get_sessions(
        self, user_id: str, project_id: str | None = None, limit: int = 50
    ) -> list[dict]:
        """Retrieve sessions for a user, optionally filtered by project."""
        self._validate_user_id(user_id)

        query = {"user_id": user_id}
        if project_id:
            query["project_id"] = project_id

        sessions = self.db_manager.find_documents(
            self.sessions_collection, query, limit=limit
        )
        logger.info(f"Retrieved {len(sessions)} sessions for user {user_id}")
        return sessions

    def get_session(self, session_id: str) -> dict | None:
        """Get session by session_id."""
        self._validate_session_id(session_id)
        sessions = self.db_manager.find_documents(
            self.sessions_collection, {"_id": session_id}
        )
        return sessions[0] if sessions else None

    def edit_session(
        self, session_id: str, title: str | None = None, tags: list[str] | None = None
    ) -> dict:
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
            self.sessions_collection,
            {"_id": session_id},
            {"$set": update_fields},
        )
        if updated_count == 0:
            raise ValueError("Session not found or no changes made")

        session = self.db_manager.find_documents(
            self.sessions_collection, {"_id": session_id}
        )
        logger.info(f"Updated session with session_id: {session_id}")
        return session[0] if session else {}

    def delete_session(self, session_id: str) -> None:
        """Delete a session and its messages."""
        self._validate_session_id(session_id)

        # Get session first to check if it exists
        session = self.get_session(session_id)
        if not session:
            raise ValueError("Session not found")

        # Delete session
        self.db_manager.delete_documents(self.sessions_collection, {"_id": session_id})

        # Delete associated messages
        self.db_manager.delete_documents(
            self.messages_collection, {"session_id": session_id}
        )

        # Delete associated feedback
        self.db_manager.delete_documents(
            self.feedback_collection, {"session_id": session_id}
        )

        # Delete associated message files
        self.db_manager.delete_documents(
            self.files_collection, {"session_id": session_id, "scope": "message"}
        )

        logger.info(f"Deleted session with session_id: {session_id} and its messages")

    # MESSAGE MANAGEMENT
    def add_message(
        self,
        session_id: str,
        message_id: str | None,
        content: dict | BaseModel,
        metadata: dict | None = None,
    ) -> dict:
        """Add a message to a session. Uses message_id as _id."""
        self._validate_session_id(session_id)
        message_id = message_id or generate_short_id("msg-")
        if not message_id.strip():
            raise ValueError("Message ID cannot be empty")

        # Get session to retrieve user_id
        session = self.get_session(session_id)
        if not session:
            raise ValueError(f"Session {session_id} not found")

        user_id = session["user_id"]

        # Check if message exists using _id
        existing_message = self.db_manager.find_documents(
            self.messages_collection, {"_id": message_id}
        )

        if existing_message:
            logger.info(f"Message with message_id {message_id} already exists.")
            return existing_message[0]

        if isinstance(content, BaseModel):
            content = content.model_dump()

        message = {
            "_id": message_id,
            "message_id": message_id,  # Ensure message_id field is set to match _id
            "user_id": user_id,  # Auto-populated from session
            "session_id": session_id,
            "content": content,
            "metadata": metadata or {},
            "created_at": datetime.utcnow(),
            "updated_at": datetime.utcnow(),
        }

        try:
            self.db_manager.insert_documents(self.messages_collection, [message])

            # Update session timestamp
            self.db_manager.update_documents(
                self.sessions_collection,
                {"_id": session_id},
                {"$set": {"updated_at": datetime.utcnow()}},
            )

            logger.info(f"Added new message with message_id: {message_id}")
            return message
        except Exception as e:
            raise ValueError(f"Failed to add message: {str(e)}")

    def get_messages(self, session_id: str, limit: int = 100) -> list[dict]:
        """Retrieve all messages for a session."""
        self._validate_session_id(session_id)

        query = {
            "session_id": session_id,
            "content": {"$exists": True},  # Ensure it's a message
        }

        messages = self.db_manager.find_documents(
            self.messages_collection, query, limit=limit
        )

        # Optimize: Fetch all feedback for this session in one query
        feedbacks = self.db_manager.find_documents(
            self.feedback_collection, {"session_id": session_id}
        )

        # Create a map of message_id -> feedback stats
        feedback_map = {}
        for f in feedbacks:
            msg_id = f.get("message_id")
            if not msg_id:
                continue

            if msg_id not in feedback_map:
                feedback_map[msg_id] = {
                    "total_likes": 0,
                    "total_dislikes": 0,
                    "user_feedback": None,
                }

            f_type = f.get("feedback_type")
            if f_type == "like":
                feedback_map[msg_id]["total_likes"] += 1
            elif f_type == "dislike":
                feedback_map[msg_id]["total_dislikes"] += 1

            # Check if this feedback belongs to the current user (if we knew the current user)
            # Since get_messages doesn't take the requesting user_id explicitly as an argument
            # (it takes session_id which implies user), we'll have to rely on logic in the
            # route handler or just return all feedback.
            # However, looking at frontend logic: it checks `user_id` against `f.user_id`.
            # For now, let's just attach the raw feedback info or the stats we calculated.
            # The frontend expects { total_likes, total_dislikes, user_feedback }

            # NOTE: We can't easily determine "user_feedback" here without the acting user_id context
            # passed into this method. But `get_messages` is usually called by the owner of the session.
            # Let's assume the session owner is the one viewing.

            # The session object has the owner's user_id.
            # Let's quickly get the session to know the owner user_id.

        # Get session to know the user_id for "user_feedback" context
        session = self.get_session(session_id)
        current_user_id = session.get("user_id") if session else None

        # Re-populate feedback map with user context
        feedback_map = {}
        for f in feedbacks:
            msg_id = f.get("message_id")
            if not msg_id:
                continue

            if msg_id not in feedback_map:
                feedback_map[msg_id] = {
                    "total_likes": 0,
                    "total_dislikes": 0,
                    "user_feedback": None,
                }

            f_type = f.get("feedback_type")
            if f_type == "like":
                feedback_map[msg_id]["total_likes"] += 1
            elif f_type == "dislike":
                feedback_map[msg_id]["total_dislikes"] += 1

            # If this feedback is from the session owner, set user_feedback
            if current_user_id and str(f.get("user_id")) == str(current_user_id):
                feedback_map[msg_id]["user_feedback"] = f_type

        # Attach feedback to messages
        for msg in messages:
            msg_id = msg.get("message_id") or str(msg.get("_id"))
            if msg_id in feedback_map:
                msg["feedback"] = feedback_map[msg_id]
            else:
                msg["feedback"] = {
                    "total_likes": 0,
                    "total_dislikes": 0,
                    "user_feedback": None,
                }

        logger.info(
            f"Retrieved {len(messages)} messages from session {session_id} with feedback"
        )
        return messages

    def get_message(self, message_id: str) -> dict | None:
        """Get message by message_id (direct _id lookup - fastest)."""
        if not message_id or not message_id.strip():
            raise ValueError("Message ID cannot be empty")

        messages = self.db_manager.find_documents(
            self.messages_collection, {"_id": message_id}
        )
        return messages[0] if messages else None

    # FEEDBACK MANAGEMENT
    def submit_feedback(
        self,
        message_id: str,
        feedback_type: str,
        comments: str | None = None,
    ) -> dict:
        """Submit feedback for a message. Feedback uses auto-generated ObjectId."""
        if not message_id or not message_id.strip():
            raise ValueError("Message ID cannot be empty")

        # Get message to retrieve session_id and user_id
        message = self.get_message(message_id)
        if not message:
            raise ValueError(f"Message {message_id} not found")

        user_id = message["user_id"]
        session_id = message["session_id"]

        feedback = {
            # ObjectId auto-generated by MongoDB (allows multiple feedbacks per message)
            "user_id": user_id,  # Auto-populated from message
            "session_id": session_id,  # Auto-populated from message
            "message_id": message_id,
            "feedback_type": feedback_type,
            "comments": comments or "",
            "created_at": datetime.utcnow(),
            "updated_at": datetime.utcnow(),
        }

        result_ids = self.db_manager.insert_documents(
            self.feedback_collection, [feedback]
        )
        if not result_ids:
            raise ValueError("Failed to submit feedback")

        feedback["_id"] = ObjectId(result_ids[0])
        logger.info(f"Submitted feedback for message_id: {message_id}")
        return feedback

    def get_feedback(self, message_id: str) -> dict | None:
        """Retrieve all feedback for a specific message."""
        if not message_id or not message_id.strip():
            raise ValueError("Message ID cannot be empty")

        feedbacks = self.db_manager.find_documents(
            self.feedback_collection,
            {"message_id": message_id},
        )
        logger.info(
            f"Retrieved {len(feedbacks)} feedback entries for message_id: {message_id}"
        )
        return feedbacks[-1] if feedbacks else None

    # FILE MANAGEMENT
    def add_file_upload(
        self,
        user_id: str,
        file_id: str,
        file_url: str,
        ocr_result: str,
        file_metadata: dict,
        status: str,
        scope: str,
        project_id: str | None = None,
        message_id: str | None = None,
    ) -> dict:
        """Add a file upload record. Uses file_id as _id."""
        self._validate_user_id(user_id)

        if not file_id or not file_id.strip():
            raise ValueError("File ID cannot be empty")
        if not file_url or not file_url.strip():
            raise ValueError("File URL cannot be empty")
        if not file_metadata or not isinstance(file_metadata, dict):
            raise ValueError("File metadata must be a valid dictionary")
        if scope not in ["project", "message"]:
            raise ValueError("Scope must be either 'project' or 'message'")
        if scope == "project" and not project_id:
            raise ValueError("Project ID is required for project-scoped files")
        if scope == "message" and not message_id:
            message_id = None  # Allow message_id to be None initially

        # Check if file exists using _id
        existing_file = self.db_manager.find_documents(
            self.files_collection, {"_id": file_id}
        )

        if existing_file:
            logger.info(f"File with file_id {file_id} already exists.")
            return existing_file[0]

        file_record = {
            "_id": file_id,
            "file_id": file_id,
            "user_id": user_id,
            "project_id": project_id,
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
            self.db_manager.insert_documents(self.files_collection, [file_record])
            logger.info(f"Added file upload record with file_id: {file_id}")
            return file_record
        except Exception as e:
            raise ValueError(f"Failed to add file upload record: {str(e)}")

    def get_files_by_scope(
        self,
        project_id: str | None = None,
        message_ids: list[str] | None = None,
        limit: int = 50,
    ) -> list[dict]:
        """Retrieve files by project or message scope."""
        if not project_id and not message_ids:
            raise ValueError("Either project_id or message_ids must be provided")

        query = {}
        if project_id:
            query["project_id"] = project_id
            query["scope"] = "project"
        elif message_ids:
            query["message_id"] = {"$in": message_ids}
            query["scope"] = "message"

        files = self.db_manager.find_documents(
            self.files_collection, query, limit=limit
        )
        logger.info(f"Retrieved {len(files)} files")
        return files

    def get_files_by_user(self, user_id: str, limit: int = 50) -> list[dict]:
        """Retrieve all message-scoped files for a user."""
        self._validate_user_id(user_id)

        query = {"user_id": user_id, "scope": "message"}
        files = self.db_manager.find_documents(
            self.files_collection, query, limit=limit
        )
        logger.info(f"Retrieved {len(files)} message files for user {user_id}")
        return files

    def get_files_by_message(self, message_id: str, limit: int = 50) -> list[dict]:
        """Retrieve all files associated with a specific message."""
        if not message_id or not message_id.strip():
            raise ValueError("Message ID cannot be empty")

        query = {"message_id": message_id}
        files = self.db_manager.find_documents(
            self.files_collection, query, limit=limit
        )
        logger.info(f"Retrieved {len(files)} files for message {message_id}")
        return files

    def get_file_by_id(self, file_id: str) -> dict | None:
        """Retrieve a specific file by file_id (direct _id lookup - fastest)."""
        if not file_id or not file_id.strip():
            raise ValueError("File ID cannot be empty")

        files = self.db_manager.find_documents(self.files_collection, {"_id": file_id})

        if files:
            logger.info(f"Retrieved file with file_id: {file_id}")
            return files[0]

        logger.warning(f"File with file_id {file_id} not found")
        return None

    def update_file_status(
        self, file_id: str, status: str, ocr_result: str | None = None
    ) -> dict:
        """Update file processing status and OCR result."""
        if not file_id or not file_id.strip():
            raise ValueError("File ID cannot be empty")

        update_fields = {"status": status, "updated_at": datetime.utcnow()}
        if ocr_result is not None:
            update_fields["ocr_result"] = ocr_result

        updated_count = self.db_manager.update_documents(
            self.files_collection,
            {"_id": file_id},
            {"$set": update_fields},
        )

        if updated_count == 0:
            raise ValueError("File not found or no changes made")

        file = self.get_file_by_id(file_id)
        logger.info(f"Updated file status for file_id: {file_id} to {status}")
        return file

    def update_file_message_id(self, file_id: str, message_id: str) -> None:
        """Associate a file with a message."""
        if not file_id or not file_id.strip():
            raise ValueError("File ID cannot be empty")
        if not message_id or not message_id.strip():
            raise ValueError("Message ID cannot be empty")

        updated_count = self.db_manager.update_documents(
            self.files_collection,
            {"_id": file_id},
            {"$set": {"message_id": message_id, "updated_at": datetime.utcnow()}},
        )

        if updated_count == 0:
            raise ValueError("File not found or no changes made")

        logger.info(f"Associated file {file_id} with message {message_id}")

    def delete_file_upload(self, file_id: str) -> None:
        """Delete a file upload record."""
        if not file_id or not file_id.strip():
            raise ValueError("File ID cannot be empty")

        deleted_count = self.db_manager.delete_documents(
            self.files_collection, {"_id": file_id}
        )
        if deleted_count == 0:
            raise ValueError("File not found")

    # SYNC MANAGEMENT
    def get_sync_data(self, user_id: str, months: int = 3) -> dict:
        """
        Retrieve all sessions and messages for a user from the last N months.
        Used for bulk synchronization to client.
        """
        self._validate_user_id(user_id)
        days = months * 30
        start_date = datetime.utcnow().timestamp() - (days * 24 * 60 * 60)
        cutoff_date = datetime.fromtimestamp(start_date)

        logger.info(f"Syncing data for user {user_id} since {cutoff_date}")

        # 1. Get recent sessions
        query = {"user_id": user_id, "updated_at": {"$gte": cutoff_date}}
        sessions = self.db_manager.find_documents(
            self.sessions_collection,
            query,
            limit=1000,  # Reasonable limit
        )

        if not sessions:
            return {"sessions": [], "messages": {}}

        # 2. Get messages for these sessions
        session_ids = [s["_id"] for s in sessions]

        messages_query = {
            "session_id": {"$in": session_ids},
            "content": {"$exists": True},
        }

        all_messages = self.db_manager.find_documents(
            self.messages_collection, messages_query, limit=10000
        )

        # 3. Get all feedback for these sessions
        feedback_query = {"session_id": {"$in": session_ids}}
        all_feedbacks = self.db_manager.find_documents(
            self.feedback_collection, feedback_query, limit=5000
        )

        # 4. Map feedback to messages
        feedback_map = {}
        for f in all_feedbacks:
            msg_id = f.get("message_id")
            if not msg_id:
                continue

            if msg_id not in feedback_map:
                feedback_map[msg_id] = {
                    "total_likes": 0,
                    "total_dislikes": 0,
                    "user_feedback": None,
                }

            f_type = f.get("feedback_type")
            if f_type == "like":
                feedback_map[msg_id]["total_likes"] += 1
            elif f_type == "dislike":
                feedback_map[msg_id]["total_dislikes"] += 1

            if str(f.get("user_id")) == str(user_id):
                feedback_map[msg_id]["user_feedback"] = f_type

        # 5. Org messages by session and attach feedback
        messages_by_session = {}

        for msg in all_messages:
            s_id = msg.get("session_id")
            if s_id not in messages_by_session:
                messages_by_session[s_id] = []

            msg_id = msg.get("message_id") or str(msg.get("_id"))

            # Attach feedback
            if msg_id in feedback_map:
                msg["feedback"] = feedback_map[msg_id]
            else:
                msg["feedback"] = {
                    "total_likes": 0,
                    "total_dislikes": 0,
                    "user_feedback": None,
                }

            messages_by_session[s_id].append(msg)

        # 6. Filter empty sessions
        # Only return sessions that have at least one message
        non_empty_sessions = []
        for session in sessions:
            if (
                session["_id"] in messages_by_session
                and len(messages_by_session[session["_id"]]) > 0
            ):
                non_empty_sessions.append(session)

        logger.info(
            f"Sync complete: {len(non_empty_sessions)} sessions, {len(all_messages)} messages"
        )

        return {"sessions": non_empty_sessions, "messages": messages_by_session}
