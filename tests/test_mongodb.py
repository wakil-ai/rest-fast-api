from unittest.mock import MagicMock, patch

import pytest
from pymongo.errors import ConnectionFailure

from app.db.mongo_handler import MongoHandler


@pytest.fixture
def mock_mongo_client():
    with patch("app.db.mongo_handler.MongoClient") as mock_client_cls:
        # Create a mock client instance
        client_instance = MagicMock()
        mock_client_cls.return_value = client_instance

        # Mock the database and collections
        db_mock = MagicMock()
        client_instance.__getitem__.return_value = db_mock  # client['db_name']

        # Mock database returning collections
        collection_mock = MagicMock()
        db_mock.__getitem__.return_value = collection_mock  # db['collection']

        yield mock_client_cls


@pytest.fixture
def mongo_handler(mock_mongo_client):
    # Reset Singleton state before test
    MongoHandler._instance = None
    MongoHandler._initialized = False

    handler = MongoHandler()
    return handler


def test_connection_success(mock_mongo_client):
    """Test successful connection initialization."""
    # Reset Singleton to ensure init runs
    MongoHandler._instance = None
    MongoHandler._initialized = False

    handler = MongoHandler()

    # Verify client initialization
    mock_mongo_client.assert_called_once()
    # Verify ping called
    handler.client.admin.command.assert_called_with("ping")


def test_connection_failure():
    """Test connection failure handling."""
    MongoHandler._instance = None
    MongoHandler._initialized = False

    with patch("app.db.mongo_handler.MongoClient") as mock_client:
        # Simulate connection failure on init or ping
        mock_client.side_effect = ConnectionFailure("Connection aborted")

        # Should not raise, just log warning (based on implementation)
        handler = MongoHandler()

        # Handler instance is created but client might not be fully usable
        # Implementation catches exception in __init__
        assert (
            handler._initialized is True
        )  # It sets true before try block? No, inside try.
        # Wait, looking at code:
        # try:
        #   if self._initialized: return
        #   self._initialized = True
        #   self.client = ...
        # except ConnectionFailure:
        #   ...

        # If it raises before settings client, handler.client might be undefined or mock if we accessed it
        # But the test just ensures no crash.


def test_crud_user(mongo_handler):
    """Test Create and Retrieve for User entity."""
    user_doc = {
        "user_id": "u1",
        "email": "test@example.com",
        "full_name": "Test User",
        "created_at": "2024-01-01",
    }

    # Mock insert return
    collection_mock = mongo_handler.db["users"]
    collection_mock.insert_one.return_value.inserted_id = "obj_id_u1"

    # Mock find return
    collection_mock.find_one.return_value = user_doc

    # 1. Create
    inserted_id = mongo_handler.insert_one("users", user_doc)
    assert inserted_id == "obj_id_u1"
    collection_mock.insert_one.assert_called_with(user_doc)

    # 2. Retrieve
    retrieved_user = mongo_handler.find_one("users", {"user_id": "u1"})
    assert retrieved_user == user_doc
    collection_mock.find_one.assert_called_with({"user_id": "u1"})


def test_crud_session(mongo_handler):
    """Test Create and Retrieve for Session."""
    session_doc = {
        "session_id": "s1",
        "user_id": "u1",
        "title": "Legal Inquiry",
        "created_at": "2024-01-01",
    }

    mongo_handler.db["sessions"].insert_one.return_value.inserted_id = "obj_id_s1"
    mongo_handler.db["sessions"].find_one.return_value = session_doc

    # Create
    mongo_handler.insert_one("sessions", session_doc)

    # Retrieve
    result = mongo_handler.find_one("sessions", {"session_id": "s1"})
    assert result == session_doc


def test_crud_message(mongo_handler):
    """Test Create and Retrieve for Message."""
    message_doc = {
        "message_id": "m1",
        "session_id": "s1",
        "role": "user",
        "content": "Hello",
        "created_at": "2024-01-01",
    }

    # Setup mocks
    mongo_handler.db["messages"].insert_one.return_value.inserted_id = "obj_id_m1"
    # Mock find_many returning a list cursor
    cursor_mock = MagicMock()
    cursor_mock.__iter__.return_value = iter([message_doc])
    # Also needs to support .limit() chaining if find_many is used
    cursor_mock.limit.return_value = [message_doc]

    mongo_handler.db["messages"].find.return_value = cursor_mock

    # Create
    mongo_handler.insert_one("messages", message_doc)

    # Retrieve many
    messages = mongo_handler.find_many("messages", {"session_id": "s1"})
    assert len(messages) == 1
    assert messages[0]["content"] == "Hello"


def test_crud_feedback(mongo_handler):
    """Test Create and Retrieve for Feedback."""
    feedback_doc = {
        "feedback_id": "f1",
        "message_id": "m1",
        "rating": 5,
        "comment": "Good job",
    }

    mongo_handler.db["feedbacks"].insert_one.return_value.inserted_id = "obj_id_f1"
    mongo_handler.db["feedbacks"].find_one.return_value = feedback_doc

    mongo_handler.insert_one("feedbacks", feedback_doc)
    result = mongo_handler.find_one("feedbacks", {"feedback_id": "f1"})
    assert result["rating"] == 5


def test_crud_project_and_file(mongo_handler):
    """Test Create and Retrieve for User Project and File."""
    project_doc = {
        "project_id": "p1",
        "user_id": "u1",
        "name": "My Case",
        "status": "active",
    }

    file_doc = {
        "file_id": "fl1",
        "project_id": "p1",
        "filename": "contract.pdf",
        "url": "http://gcs/bucket/contract.pdf",
    }

    # Test Project
    mongo_handler.db["projects"].insert_one.return_value.inserted_id = "obj_id_p1"
    mongo_handler.db["projects"].find_one.return_value = project_doc

    mongo_handler.insert_one("projects", project_doc)
    proj = mongo_handler.find_one("projects", {"project_id": "p1"})
    assert proj["name"] == "My Case"

    # Test File
    mongo_handler.db["files"].insert_one.return_value.inserted_id = "obj_id_fl1"
    mongo_handler.db["files"].find_many.return_value = [
        file_doc
    ]  # find_documents returns list directly?

    # Check find_documents implementation:
    # cursor = collection.find(...)
    # return list(cursor)

    cursor_mock = MagicMock()
    cursor_mock.__iter__.return_value = iter([file_doc])
    # For find_documents, it calls find -> list(cursor)
    mongo_handler.db["files"].find.return_value = cursor_mock

    mongo_handler.insert_one("files", file_doc)

    files = mongo_handler.find_documents("files", {"project_id": "p1"})
    assert len(files) == 1
    assert files[0]["filename"] == "contract.pdf"
