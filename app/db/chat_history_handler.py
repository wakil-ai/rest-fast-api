# app/db/init_chat_history.py

from app.db.db_manager import DBManager
from app.core.logger import logger


def create_chat_history_indexes():
    """
    Create MongoDB indexes for chat history collections
    """
    db_manager = DBManager()

    try:
        # Get collections
        conversations_collection = db_manager.mongo_handler.db["chat_conversations"]
        feedback_collection = db_manager.mongo_handler.db["chat_feedback"]

        # Create indexes for chat_conversations
        logger.info("Creating indexes for chat_conversations collection...")
        conversations_collection.create_index("session_id")
        conversations_collection.create_index([("created_at", -1)])
        conversations_collection.create_index("messages.message_id")
        conversations_collection.create_index([("session_id", 1), ("updated_at", -1)])
        conversations_collection.create_index([("status", 1), ("created_at", -1)])

        # Create indexes for chat_feedback
        logger.info("Creating indexes for chat_feedback collection...")
        feedback_collection.create_index("session_id")
        feedback_collection.create_index("message_id")
        feedback_collection.create_index([("timestamp", -1)])

        logger.info("Successfully created all chat history indexes")

    except Exception as e:
        logger.error(f"Error creating chat history indexes: {str(e)}")
        raise
    finally:
        db_manager.mongo_handler.close_connection()


if __name__ == "__main__":
    create_chat_history_indexes()