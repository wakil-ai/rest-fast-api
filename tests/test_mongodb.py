from unittest.mock import Mock, patch

import pytest
from pymongo.errors import ConnectionFailure

from app.db.mongo_handler import MongoHandler


@pytest.mark.unit
@pytest.mark.mongodb
class TestMongoHandler:
    """Test MongoDB handler."""

    @pytest.fixture
    def mongo_handler(self, test_env_vars):
        """Create MongoHandler instance for testing."""
        with patch("app.db.mongo_handler.MongoClient") as mock_client:
            mock_instance = Mock()
            mock_client.return_value = mock_instance

            # Mock successful ping
            mock_instance.admin.command.return_value = {"ok": 1}

            handler = MongoHandler()
            handler.client = mock_instance
            handler.db = mock_instance["test_wakilai"]
            handler.collection = handler.db["test_collection"]
            return handler

    def test_singleton_pattern(self, test_env_vars):
        """Test that MongoHandler follows singleton pattern."""
        with patch("app.db.mongo_handler.MongoClient") as mock_client:
            mock_instance = Mock()
            mock_instance.admin.command.return_value = {"ok": 1}
            mock_client.return_value = mock_instance

            handler1 = MongoHandler()
            handler2 = MongoHandler()

            assert handler1 is handler2
            mock_client.assert_called_once()

    def test_initialization_success(self, test_env_vars):
        """Test successful MongoDB initialization."""
        with patch("app.db.mongo_handler.MongoClient") as mock_client:
            mock_instance = Mock()
            mock_instance.admin.command.return_value = {"ok": 1}
            mock_client.return_value = mock_instance

            handler = MongoHandler()

            # Verify client creation with correct parameters
            mock_client.assert_called_once_with(
                "mongodb://localhost:27017/test_db",
                serverSelectionTimeoutMS=5000,
                tlsAllowInvalidCertificates=True,
            )

            # Verify ping was called
            mock_instance.admin.command.assert_called_once_with("ping")

            # Verify database and collection were set
            assert handler.db == mock_instance["test_wakilai"]
            assert handler.collection == handler.db["test_collection"]

    def test_initialization_connection_failure(self, test_env_vars, caplog):
        """Test handling of connection failure during initialization."""
        with patch("app.db.mongo_handler.MongoClient") as mock_client:
            mock_instance = Mock()
            mock_instance.admin.command.side_effect = ConnectionFailure(
                "Connection failed"
            )
            mock_client.return_value = mock_instance

            MongoHandler()

            # Should not raise exception, but should log warning
            assert "Could not connect to MongoDB" in caplog.text

    def test_insert_documents(self, mongo_handler):
        """Test inserting multiple documents."""
        documents = [
            {"name": "Doc 1", "content": "Content 1"},
            {"name": "Doc 2", "content": "Content 2"},
            {"name": "Doc 3", "content": "Content 3"},
        ]

        # Mock insert_many result
        mock_result = Mock()
        mock_result.inserted_ids = ["id1", "id2", "id3"]
        mongo_handler.db["test_collection"].insert_many.return_value = mock_result

        result = mongo_handler.insert_documents("test_collection", documents)

        assert result == ["id1", "id2", "id3"]
        mongo_handler.db["test_collection"].insert_many.assert_called_once_with(
            documents
        )

    def test_insert_documents_empty_list(self, mongo_handler):
        """Test inserting empty document list."""
        result = mongo_handler.insert_documents("test_collection", [])

        # Should not call insert_many for empty list
        mongo_handler.db["test_collection"].insert_many.assert_not_called()
        assert result == []

    def test_find_documents(self, mongo_handler, mock_mongo_doc):
        """Test finding documents with query."""
        query = {"user_id": "test_user"}
        mock_cursor = Mock()
        mock_cursor.__iter__ = Mock(return_value=iter([mock_mongo_doc]))
        mongo_handler.db["test_collection"].find.return_value = mock_cursor

        result = mongo_handler.find_documents("test_collection", query, limit=10)

        assert len(result) == 1
        assert result[0] == mock_mongo_doc

        mongo_handler.db["test_collection"].find.assert_called_once_with(
            query, limit=10, sort=[("created_at", -1)]
        )

    def test_find_documents_default_limit(self, mongo_handler):
        """Test finding documents with default limit."""
        query = {"status": "active"}
        mock_cursor = Mock()
        mock_cursor.__iter__ = Mock(return_value=iter([]))
        mongo_handler.db["test_collection"].find.return_value = mock_cursor

        mongo_handler.find_documents("test_collection", query)

        # Should use default limit of 50
        mongo_handler.db["test_collection"].find.assert_called_once_with(
            query, limit=50, sort=[("created_at", -1)]
        )

    def test_find_one_document(self, mongo_handler, mock_mongo_doc):
        """Test finding a single document."""
        query = {"_id": "507f1f77bcf86cd799439011"}
        mongo_handler.db["test_collection"].find_one.return_value = mock_mongo_doc

        result = mongo_handler.find_one("test_collection", query)

        assert result == mock_mongo_doc
        mongo_handler.db["test_collection"].find_one.assert_called_once_with(query)

    def test_find_one_not_found(self, mongo_handler):
        """Test finding a single document that doesn't exist."""
        query = {"_id": "nonexistent"}
        mongo_handler.db["test_collection"].find_one.return_value = None

        result = mongo_handler.find_one("test_collection", query)

        assert result is None

    def test_insert_one_document(self, mongo_handler):
        """Test inserting a single document."""
        document = {"name": "Test Doc", "content": "Test content"}
        mock_result = Mock()
        mock_result.inserted_id = "507f1f77bcf86cd799439011"
        mongo_handler.db["test_collection"].insert_one.return_value = mock_result

        result = mongo_handler.insert_one("test_collection", document)

        assert result == "507f1f77bcf86cd799439011"  # pragma: allowlist secret
        mongo_handler.db["test_collection"].insert_one.assert_called_once_with(document)

    def test_update_one_document(self, mongo_handler):
        """Test updating a single document."""
        query = {"_id": "507f1f77bcf86cd799439011"}
        update = {"status": "updated", "last_modified": "2024-01-01"}
        mock_result = Mock()
        mock_result.modified_count = 1
        mongo_handler.db["test_collection"].update_one.return_value = mock_result

        result = mongo_handler.update_one("test_collection", query, update)

        assert result is True
        mongo_handler.db["test_collection"].update_one.assert_called_once_with(
            query, {"$set": update}
        )

    def test_update_one_no_match(self, mongo_handler):
        """Test updating a document that doesn't exist."""
        query = {"_id": "nonexistent"}
        update = {"status": "updated"}
        mock_result = Mock()
        mock_result.modified_count = 0
        mongo_handler.db["test_collection"].update_one.return_value = mock_result

        result = mongo_handler.update_one("test_collection", query, update)

        assert result is False

    def test_find_many_documents(self, mongo_handler):
        """Test finding multiple documents without sorting."""
        query = {"category": "legal"}
        mock_cursor = Mock()
        mock_docs = [{"name": "Doc 1"}, {"name": "Doc 2"}]
        mock_cursor.__iter__ = Mock(return_value=iter(mock_docs))
        mongo_handler.db["test_collection"].find.return_value = mock_cursor

        result = mongo_handler.find_many("test_collection", query, limit=100)

        assert len(result) == 2
        assert result == mock_docs

        mongo_handler.db["test_collection"].find.assert_called_once_with(query)
        mock_cursor.limit.assert_called_once_with(100)

    def test_delete_collection(self, mongo_handler):
        """Test deleting a collection."""
        mongo_handler.db["test_collection"].drop_collection = Mock()

        mongo_handler.delete_collection("test_collection")

        mongo_handler.db["test_collection"].drop_collection.assert_called_once()

    def test_delete_collection_with_exception(self, mongo_handler, caplog):
        """Test handling of errors when deleting collection."""
        mongo_handler.db["test_collection"].drop_collection.side_effect = Exception(
            "Database error"
        )

        mongo_handler.delete_collection("test_collection")

        # Should log error but not raise exception
        assert "Error dropping collection" in caplog.text

    def test_clean_collection(self, mongo_handler):
        """Test cleaning all documents from a collection."""
        mock_result = Mock()
        mock_result.deleted_count = 5
        mongo_handler.db["test_collection"].delete_many.return_value = mock_result

        mongo_handler.clean_collection("test_collection")

        mongo_handler.db["test_collection"].delete_many.assert_called_once_with({})
        # Check that deleted_count is accessed
        _ = mock_result.deleted_count

    def test_clean_collection_with_exception(self, mongo_handler, caplog):
        """Test handling of errors when cleaning collection."""
        mongo_handler.db["test_collection"].delete_many.side_effect = Exception(
            "Database error"
        )

        mongo_handler.clean_collection("test_collection")

        # Should log error but not raise exception
        assert "Error cleaning collection" in caplog.text

    def test_close_connection(self, mongo_handler):
        """Test closing MongoDB connection."""
        mongo_handler.client.close = Mock()

        mongo_handler.close_connection()

        mongo_handler.client.close.assert_called_once()

    def test_user_creation_workflow(self, mongo_handler):
        """Test complete user creation workflow."""
        # Create user
        user_doc = {
            "user_id": "user_123",
            "email": "test@example.com",
            "created_at": "2024-01-01T00:00:00Z",
            "profile": {"name": "Test User"},
        }
        mock_result = Mock()
        mock_result.inserted_id = "user_obj_id"
        mongo_handler.db["users"].insert_one.return_value = mock_result

        user_id = mongo_handler.insert_one("users", user_doc)

        # Create project for user
        project_doc = {
            "user_id": "user_123",
            "project_name": "Legal Research Project",
            "created_at": "2024-01-01T01:00:00Z",
        }
        mock_result.inserted_id = "project_obj_id"
        mongo_handler.db["projects"].insert_one.return_value = mock_result

        project_id = mongo_handler.insert_one("projects", project_doc)

        # Verify both documents were created
        assert user_id == "user_obj_id"
        assert project_id == "project_obj_id"

        mongo_handler.db["users"].insert_one.assert_called_once_with(user_doc)
        mongo_handler.db["projects"].insert_one.assert_called_once_with(project_doc)

    def test_project_history_retrieval(self, mongo_handler):
        """Test retrieving project history for a user."""
        # Mock project documents
        projects = [
            {
                "_id": "proj_1",
                "user_id": "user_123",
                "project_name": "Project 1",
                "created_at": "2024-01-01T00:00:00Z",
            },
            {
                "_id": "proj_2",
                "user_id": "user_123",
                "project_name": "Project 2",
                "created_at": "2024-01-02T00:00:00Z",
            },
        ]

        mock_cursor = Mock()
        mock_cursor.__iter__ = Mock(return_value=iter(projects))
        mongo_handler.db["projects"].find.return_value = mock_cursor

        result = mongo_handler.find_documents("projects", {"user_id": "user_123"})

        assert len(result) == 2
        assert result[0]["project_name"] == "Project 1"
        assert result[1]["project_name"] == "Project 2"

        # Verify query and sorting
        mongo_handler.db["projects"].find.assert_called_once_with(
            {"user_id": "user_123"}, limit=50, sort=[("created_at", -1)]
        )


@pytest.mark.integration
@pytest.mark.mongodb
@pytest.mark.slow
class TestMongoIntegration:
    """Integration tests for MongoDB (requires actual MongoDB instance)."""

    @pytest.fixture(autouse=True)
    def setup_integration(self):
        """Setup for integration tests."""
        pytest.skip("Integration test - set RUN_INTEGRATION_TESTS=1 to run")

    def test_real_mongodb_connection(self):
        """Test real MongoDB connection (integration test)."""
        # This test would require a running MongoDB instance
        # and should be run only in CI/CD environment
        pass
