import asyncio
from datetime import datetime, timezone
from typing import Any

from fastapi import HTTPException, status
from pydantic import BaseModel


async def delete_agent_thread(*, user_id: str, session_id: str) -> bool:
    return False


from core.config import settings
from core.dependencies import (
    get_bitrix24_service,
    get_db_manager,
    get_organization_service,
)
from core.exceptions import (
    InvalidInputError,
    MessageNotFoundError,
    SessionNotFoundError,
    UserNotFoundError,
)
from core.logger import logger
from models.chat_history import SessionStatus, ShareResponse
from utils.user_management import clean_for_mongodb, generate_short_id

# predicate that excludes soft-deleted (archived) documents from
# user-facing reads. Merge into a query with ``{**_ACTIVE_ONLY, ...}``. Absence of the
# field counts as active, so this is safe for pre-archive data. Deliberately NOT applied
# to identity/raw lookups — ``get_user``, ``get_user_by_external_id``,
# ``is_user_archived``, ``_ensure_user_exists`` — which auth and the archive
# guard rely on seeing archived docs.
_ACTIVE_ONLY: dict[str, Any] = {"archived": {"$ne": True}}


def _normalize_org_scope(org_id: str | None) -> str | None:
    """Blank org_id means the personal profile.

    Without this an empty string is falsy enough to skip the membership check but
    still gets stored, and then matches neither the personal filter (absent/null)
    nor any org filter — the session becomes permanently invisible.
    """
    return (org_id or "").strip() or None


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
        self.projects_collection = settings.PROJECTS_COLLECTION
        self.token_counting_collection = settings.TOKEN_COUNTING_COLLECTION
        self.bitrix24_service = get_bitrix24_service()

        # Create collections and indexes when a running loop is available.
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            loop = None
        if loop is not None:
            loop.create_task(self._init_collections())

    async def _init_collections(self):
        """Initialize necessary database collections (async)."""
        for collection in [
            self.users_collection,
            self.sessions_collection,
            self.messages_collection,
            self.files_collection,
            self.projects_collection,
            self.token_counting_collection,
        ]:
            await self.db_manager.create_collection(collection)

        # Create indexes for better query performance
        await self._create_indexes()

    async def _create_indexes(self):
        """Create necessary indexes for collections (async)."""
        try:
            # Sessions - query by user within one org scope
            await self.db_manager.mongo_handler.db[
                self.sessions_collection
            ].create_index(
                [("user_id", 1), ("org_id", 1), ("status", 1), ("updated_at", -1)]
            )

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
            await self.db_manager.mongo_handler.db[self.files_collection].create_index(
                [("project_id", 1), ("scope", 1)]
            )

            await self.db_manager.mongo_handler.db[
                self.projects_collection
            ].create_index([("owner_id", 1), ("updated_at", -1)])

            await self.db_manager.mongo_handler.db[
                self.sessions_collection
            ].create_index([("project_id", 1), ("updated_at", -1)])

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

        now = datetime.now(timezone.utc)

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

    async def assert_session_access(self, session_id: str, user_id: str) -> dict:
        """The single gate for reaching a session directly by id.

        Existence, ownership, and — for an org session — live membership. Scoping
        the list endpoint is not enough on its own: without this, anyone holding a
        session id keeps access after being removed from the organization.
        """
        session = await self._ensure_session_exists(session_id)
        if session.get("user_id") != user_id:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN, detail="Access denied"
            )
        org_id = session.get("org_id")
        if org_id:
            await get_organization_service().assert_org_member(org_id, user_id)
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
            self.sessions_collection, {"_id": session_id, **_ACTIVE_ONLY}
        )
        if not session:
            raise SessionNotFoundError(session_id)
        return session[0]

    async def _ensure_message_exists(self, message_id: str) -> dict:
        """Ensure message exists in database, raise MessageNotFoundError if not."""
        if not message_id or not message_id.strip():
            raise InvalidInputError("Message ID cannot be empty")

        message = await self.db_manager.find_documents(
            self.messages_collection, {"_id": message_id, **_ACTIVE_ONLY}
        )
        if not message:
            raise MessageNotFoundError(message_id)
        return message[0]

    async def _ensure_file_attachments_for_user(
        self, file_ids: list[str] | None, user_id: str
    ) -> None:
        """Require each attachment to exist and belong to the given user."""

        unique_file_ids = list(dict.fromkeys(file_ids or []))
        if not unique_file_ids:
            return

        files = await self.db_manager.find_documents(
            self.files_collection,
            {"_id": {"$in": unique_file_ids}, **_ACTIVE_ONLY},
            limit=len(unique_file_ids),
        )
        files_by_id = {file["_id"]: file for file in files}

        for file_id in unique_file_ids:
            file = files_by_id.get(file_id)
            if file is None:
                raise InvalidInputError(f"File {file_id} not found")
            if file.get("user_id") != user_id:
                raise InvalidInputError(f"File {file_id} does not belong to this user")

    # Users Management
    async def _create_bitrix_lead_if_needed(self, user: dict) -> dict:
        """Create one Bitrix24 lead for a user with a phone number."""
        if not user or user.get("bitrix24_lead_id"):
            return user

        lead_id = await self.bitrix24_service.create_lead_for_user(user)
        if lead_id is None:
            return user

        lead_update = {
            "bitrix24_lead_id": str(lead_id),
            "bitrix24_lead_created_at": datetime.utcnow(),
            "updated_at": datetime.utcnow(),
        }
        try:
            await self.db_manager.update_documents(
                self.users_collection,
                {"_id": user["_id"]},
                {"$set": lead_update},
            )
            user.update(lead_update)
        except Exception as exc:
            logger.error(
                f"Failed to store Bitrix24 lead ID for user {user.get('_id')}: {exc}",
                exc_info=True,
            )
        return user

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
            "signup_credits_used": 0,
            "created_at": datetime.now(timezone.utc),
            "updated_at": datetime.now(timezone.utc),
        }

        try:
            # Clean data for MongoDB (convert UUIDs, Decimals, etc.)
            user = clean_for_mongodb(user)
            await self.db_manager.insert_documents(self.users_collection, [user])
            logger.info(f"Created new user with user_id: {user_id}")
            await self._create_bitrix_lead_if_needed(user)
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

    async def get_user_auth_status(self, user_id: str) -> dict | None:
        """Return only the user fields required by request authentication."""
        if not user_id:
            return None
        user = await self.db_manager.mongo_handler.db[
            self.users_collection
        ].find_one(
            {"_id": user_id},
            {"is_blocked": 1, "archived": 1},
        )
        if not user:
            return None
        return {
            "exists": True,
            "is_blocked": bool(user.get("is_blocked")),
            "archived": bool(user.get("archived")),
        }

    async def is_user_archived(self, user_id: str) -> bool:
        """Return True if the user exists and is archived (soft-deleted).

        Purpose-built for the runtime archive guard. Queries the users collection
        directly with a minimal projection, so it is NOT affected by any archived
        filtering applied to normal reads. Unknown users return
        False (nothing to block; not-found is handled by the read path itself).
        """
        if not user_id:
            return False
        doc = await self.db_manager.mongo_handler.db[self.users_collection].find_one(
            {"_id": user_id}, {"archived": 1}
        )
        return bool(doc and doc.get("archived"))

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
            "updated_at": datetime.now(timezone.utc),
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
            "updated_at": datetime.now(timezone.utc),
        }

        # Even if modified_count is 0 (e.g. same value), we still return the current document.
        await self.db_manager.update_documents(
            self.users_collection,
            {"_id": user_id},
            {"$set": update_fields},
        )

        user = await self.get_user(user_id)
        if user:
            await self._create_bitrix_lead_if_needed(user)
        return user

    async def block_user(self, user_id: str, reason: str | None = None) -> dict | None:
        """Block a user so they cannot log in until unblocked.

        If the user does not exist yet, a minimal user document is created.
        """
        if not user_id or not user_id.strip():
            raise InvalidInputError("User ID cannot be empty")

        now = datetime.now(timezone.utc)
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

        now = datetime.now(timezone.utc)

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
        project_id: str | None = None,
        org_id: str | None = None,
    ) -> dict:
        """Create or retrieve an existing session. Uses session_id as _id."""

        await self._ensure_user_exists(user_id)

        # The scope is stamped once here and never changed: moving a personal
        # session into an org later would leak its history across that boundary.
        org_id = _normalize_org_scope(org_id)
        if org_id:
            await get_organization_service().assert_org_member(org_id, user_id)

        # Generate session ID using uuid7 with 'ses-' prefix
        session_id = generate_short_id("ses-", type="uuid7")

        # Session model
        session = {
            "_id": session_id,
            "user_id": user_id,
            "session_id": session_id,  # Ensure session_id field is set to match _id
            "project_id": project_id,
            "org_id": org_id,
            "title": title or "New Chat",
            "tags": tags or [],
            "status": SessionStatus.draft.value,
            "activated_at": None,
            "created_at": datetime.now(timezone.utc),
            "updated_at": datetime.now(timezone.utc),
        }

        try:
            await self.db_manager.insert_documents(self.sessions_collection, [session])
            logger.info(f"Created new session with session_id: {session_id}")
            return session
        except Exception as e:
            raise InvalidInputError(f"Failed to create session: {str(e)}")

    # org_id needs no separate proof of who is asking: verify_user_or_service_auth
    # (security/dependencies.py) cross-checks any user_id in the path, query or body
    # against the JWT subject before the handler runs, so a bearer caller cannot name
    # someone else's user_id here. Service-key callers are trusted server-to-server
    # (docs/dt-team-integration.md) and send no org_id. assert_org_member is the real
    # gate on both paths — it demands a live membership row either way.
    async def get_sessions(
        self,
        user_id: str,
        org_id: str | None = None,
        limit: int = 50,
        skip: int = 0,
    ) -> list[dict]:
        """Active non-project sessions for a user, within one scope.

        ``org_id=None`` means the personal profile and excludes every org session,
        so switching scope in the frontend never mixes the two.
        """
        await self._ensure_user_exists(user_id)

        # Checked here rather than in the route so every caller is covered — this
        # function owns the query, so it owns the guard. Mirrors create_session.
        org_id = _normalize_org_scope(org_id)
        if org_id:
            await get_organization_service().assert_org_member(org_id, user_id)

        # ponytail: sessions created before org scoping have no org_id key at all,
        # so "personal" must match absent as well as null. Same trick as project_id.
        scope = (
            {"org_id": org_id}
            if org_id
            else {"$or": [{"org_id": {"$exists": False}}, {"org_id": None}]}
        )

        sessions = await self.db_manager.find_documents(
            self.sessions_collection,
            {
                "user_id": user_id,
                "status": SessionStatus.active.value,
                # Two scope predicates, so both live under $and — a query dict
                # cannot carry two top-level $or keys.
                "$and": [
                    {
                        "$or": [
                            {"project_id": {"$exists": False}},
                            {"project_id": None},
                        ]
                    },
                    scope,
                ],
                **_ACTIVE_ONLY,
            },
            limit=limit,
            skip=skip,
        )

        return sessions

    async def get_sessions_by_project(
        self, project_id: str, limit: int = 50, skip: int = 0
    ) -> list[dict]:
        """Active project sessions that have at least one persisted message."""
        if not project_id or not project_id.strip():
            raise InvalidInputError("Project ID cannot be empty")

        collection = self.db_manager.mongo_handler.db[self.sessions_collection]
        pipeline: list[dict[str, Any]] = [
            {
                "$match": {
                    "project_id": project_id,
                    "status": SessionStatus.active.value,
                    **_ACTIVE_ONLY,
                }
            },
            {
                "$lookup": {
                    "from": self.messages_collection,
                    "let": {"session_id": "$_id"},
                    "pipeline": [
                        {"$match": {"$expr": {"$eq": ["$session_id", "$$session_id"]}}},
                        {"$limit": 1},
                        {"$project": {"_id": 1}},
                    ],
                    "as": "linked_messages",
                }
            },
            {"$match": {"linked_messages.0": {"$exists": True}}},
            {"$project": {"linked_messages": 0}},
            {"$sort": {"updated_at": -1}},
            {"$skip": skip},
            {"$limit": limit},
        ]
        cursor = collection.aggregate(pipeline)
        return await cursor.to_list(length=limit)

    async def get_session(self, session_id: str) -> dict | None:
        """Get session by session_id."""
        if not session_id or not session_id.strip():
            raise InvalidInputError("Session ID cannot be empty")

        sessions = await self.db_manager.find_documents(
            self.sessions_collection, {"_id": session_id, **_ACTIVE_ONLY}
        )
        return sessions[0] if sessions else None

    async def attach_session_to_project(self, session_id: str, project_id: str) -> dict:
        """Set ``project_id`` on a session (e.g. first chat message in a project)."""
        await self._ensure_session_exists(session_id)
        if not project_id or not project_id.strip():
            raise InvalidInputError("Project ID cannot be empty")
        now = datetime.now(timezone.utc)
        await self.db_manager.update_documents(
            self.sessions_collection,
            {"_id": session_id},
            {"$set": {"project_id": project_id, "updated_at": now}},
        )
        session = await self.get_session(session_id)
        return session or {}

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

        update_fields["updated_at"] = datetime.now(timezone.utc)
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

        session = await self._ensure_session_exists(session_id)
        user_id = session.get("user_id")

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

        if user_id:
            await delete_agent_thread(user_id=str(user_id), session_id=session_id)

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

        await self._ensure_file_attachments_for_user(file_ids, user_id)

        message = {
            "_id": resolved_message_id,
            "user_id": user_id,  # Auto-populated from session
            "session_id": session_id,
            "message_id": resolved_message_id,  # Ensure message_id field is set to match _id
            "file_ids": file_ids or [],  # Can be empty list for non-file messages
            "content": content,
            "metadata": metadata or {},
            "created_at": datetime.now(timezone.utc),
            "updated_at": datetime.now(timezone.utc),
        }

        try:
            await self.db_manager.insert_documents(self.messages_collection, [message])

            now = datetime.now(timezone.utc)
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

        await self._ensure_file_attachments_for_user(file_ids, user_id)

        now = datetime.now(timezone.utc)
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
            **_ACTIVE_ONLY,
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
            self.messages_collection, {"_id": message_id, **_ACTIVE_ONLY}
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
            "created_at": datetime.now(timezone.utc),
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
                    "shared_at": datetime.now(timezone.utc),
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
            self.messages_collection, {"share_id": share_id, **_ACTIVE_ONLY}
        )
        message = message[0] if message else None
        if not message:
            raise InvalidInputError(f"Share {share_id} not found")

        created_at = message.get("shared_at") or datetime.now(timezone.utc)

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
                    "updated_at": datetime.now(timezone.utc),
                }
            },
        )
        logger.info(f"Submitted feedback for message_id: {message_id}")
        return {
            "message_id": message_id,
            "feedback_type": feedback_type,
            "feedback_content": feedback_content,
        }

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
        project_id: str | None = None,
        session_id: str | None = None,
        webhook_url: str | None = None,
    ) -> dict:
        """Add a file upload record. Uses file_id as _id."""

        await self._ensure_user_exists(user_id)

        if not file_id or not file_id.strip():
            raise InvalidInputError("File ID cannot be empty")
        if not file_url or not file_url.strip():
            raise InvalidInputError("File URL cannot be empty")
        if not file_metadata or not isinstance(file_metadata, dict):
            raise InvalidInputError("File metadata must be a valid dictionary")
        if scope not in ["message", "project"]:
            raise InvalidInputError("Scope must be 'message' or 'project'")
        if scope == "project" and not project_id:
            raise InvalidInputError("project_id is required for project-scoped files")
        if scope == "message" and not message_id:
            message_id = None  # Allow message_id to be None initially

        # Check if file exists using _id
        existing_file = await self.db_manager.find_documents(
            self.files_collection, {"_id": file_id}
        )

        if existing_file:
            logger.info(f"File with file_id {file_id} already exists.")
            return existing_file[0]

        file_record: dict[str, Any] = {
            "_id": file_id,
            "file_id": file_id,
            "user_id": user_id,
            "message_id": message_id,
            "scope": scope,
            "file_url": file_url,
            "ocr_result": ocr_result,
            "file_metadata": file_metadata,
            "status": status,
            "created_at": datetime.now(timezone.utc),
            "updated_at": datetime.now(timezone.utc),
        }
        if project_id:
            file_record["project_id"] = project_id
        if session_id:
            file_record["session_id"] = session_id
        if webhook_url:
            file_record["webhook_url"] = webhook_url

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
            **_ACTIVE_ONLY,
        }

        files = await self.db_manager.find_documents(
            self.files_collection, query, limit=limit
        )
        logger.info(f"Retrieved {len(files)} files")
        return files

    async def get_files_by_user(self, user_id: str, limit: int = 50) -> list[dict]:
        """Retrieve all message-scoped files for a user."""

        await self._ensure_user_exists(user_id)

        query = {"user_id": user_id, "scope": "message", **_ACTIVE_ONLY}
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

        query = {"message_id": message_id, **_ACTIVE_ONLY}
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
            self.files_collection, {"_id": file_id, **_ACTIVE_ONLY}
        )

        if files:
            logger.info(f"Retrieved file with file_id: {file_id}")
            return files[0]

        logger.warning(f"File with file_id {file_id} not found")
        return None

    async def get_file_by_content_hash(
        self, content_hash: str, user_id: str
    ) -> dict | None:
        """Retrieve a file by raw content hash scoped to a specific user."""
        if not content_hash or not content_hash.strip():
            raise InvalidInputError("Content hash cannot be empty")
        if not user_id or not user_id.strip():
            raise InvalidInputError("User ID cannot be empty")

        files = await self.db_manager.find_documents(
            self.files_collection,
            {
                "file_metadata.file_content_hash": content_hash,
                "user_id": user_id,
            },
            limit=1,
        )
        return files[0] if files else None

    async def get_file_by_content_hash_for_project(
        self, content_hash: str, user_id: str, project_id: str
    ) -> dict | None:
        """Same as get_file_by_content_hash but isolated per project."""
        if not project_id or not project_id.strip():
            raise InvalidInputError("Project ID cannot be empty")
        if not content_hash or not content_hash.strip():
            raise InvalidInputError("Content hash cannot be empty")
        if not user_id or not user_id.strip():
            raise InvalidInputError("User ID cannot be empty")

        files = await self.db_manager.find_documents(
            self.files_collection,
            {
                "file_metadata.file_content_hash": content_hash,
                "user_id": user_id,
                "project_id": project_id,
                "scope": "project",
            },
            limit=1,
        )
        return files[0] if files else None

    async def get_files_by_project(
        self, project_id: str, limit: int = 100
    ) -> list[dict]:
        """Project-scoped uploads (Mongo)."""
        if not project_id or not project_id.strip():
            raise InvalidInputError("Project ID cannot be empty")
        return await self.db_manager.find_documents(
            self.files_collection,
            {"project_id": project_id, "scope": "project"},
            limit=limit,
        )

    async def update_file_status(
        self, file_id: str, status: str, ocr_result: str | None = None
    ) -> dict:
        """Update file processing status and OCR result."""

        if not file_id or not file_id.strip():
            raise InvalidInputError("File ID cannot be empty")

        update_fields = {"status": status, "updated_at": datetime.now(timezone.utc)}
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

    async def update_file_metadata_fields(
        self, file_id: str, fields: dict[str, Any]
    ) -> dict:
        """Update selected top-level or dotted fields on a file record."""

        if not file_id or not file_id.strip():
            raise InvalidInputError("File ID cannot be empty")
        if not fields:
            raise InvalidInputError("No file metadata fields provided")

        update_fields = {**fields, "updated_at": datetime.now(timezone.utc)}
        await self.db_manager.update_documents(
            self.files_collection,
            {"_id": file_id},
            {"$set": update_fields},
        )

        file = await self.get_file_by_id(file_id)
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
            {
                "$set": {
                    "message_id": message_id,
                    "updated_at": datetime.now(timezone.utc),
                }
            },
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
