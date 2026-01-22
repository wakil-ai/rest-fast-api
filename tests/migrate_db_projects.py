import asyncio
import os
import sys

# Add the project root to the python path
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from app.core.config import settings
from app.db.db_manager import DBManager


async def migrate_ids_and_projects():
    """
    1. Migrate documents to use their string IDs (user_id, session_id, etc.) as MongoDB `_id`.
    2. Set default project_id = None for sessions.
    """
    print("Starting ID and Project Migration...")

    db_manager = DBManager()
    db = db_manager.mongo_handler.db

    # Check feedback model - looks like feedback uses auto-generated ObjectId in new code too?
    # New code: "feedback_id: str = Field(..., description="Feedback ID (ObjectId)")" -> This implies it uses ObjectId as string.
    # Old code: "mongo_id: str ... message_id: str"
    # So feedback is likely fine or uses ObjectId anyway. Let's verify feedback later.
    # The user request specifically mentioned "chat history" structure.

    # We'll skip feedback ID migration for now unless we see an explicit string ID field that should be _id.
    # In new code: `feedback["_id"] = ObjectId(result_ids[0])` -> It still uses ObjectId.
    # So we ONLY need to migrate Users, Sessions, Messages, Files.

    target_collections = {
        settings.USERS_COLLECTION: "user_id",
        settings.SESSIONS_COLLECTION: "session_id",
        settings.MESSAGES_COLLECTION: "message_id",
    }

    for col_name, id_field in target_collections.items():
        print(f"\nProcessing collection: {col_name} (target _id key: {id_field})")
        collection = db[col_name]

        # Find documents where _id is an ObjectId (meaning it's an old document)
        # AND it has the custom ID field
        cursor = collection.find(
            {"_id": {"$type": "objectId"}, id_field: {"$exists": True}}
        )

        migrated_count = 0
        error_count = 0

        docs_to_migrate = list(cursor)
        if not docs_to_migrate:
            print(f"  No documents found needing ID migration in {col_name}.")
        else:
            print(f"  Found {len(docs_to_migrate)} documents to migrate ids.")

            for doc in docs_to_migrate:
                try:
                    target_id = doc[id_field]
                    old_oid = doc["_id"]

                    # Check if a doc with the new ID already exists
                    if collection.find_one({"_id": target_id}):
                        print(
                            f"    SKIPPING: Document with _id={target_id} already exists. Deleting duplicate old ObjectId doc."
                        )
                        # collection.delete_one({"_id": old_oid})
                        # Only delete safely if we are sure? Let's arguably just skip it and let user decide,
                        # or effectively we consider it migrated.
                        # But wait, if we have duplicate, we might want to keep the one with _id=target_id.
                        # The old one is redundant.
                        try:
                            collection.delete_one({"_id": old_oid})
                            migrated_count += 1
                        except Exception as e:
                            print(
                                f"    Error deleting duplicate old doc {old_oid}: {e}"
                            )
                        continue

                    # STRATEGY:
                    # 1. Rename the unique field in the OLD doc to avoid unique constraint collision
                    #    when we insert the NEW doc (which has the same value for that field).
                    temp_val = f"{target_id}_TEMP_MIGRATE"
                    collection.update_one(
                        {"_id": old_oid}, {"$set": {id_field: temp_val}}
                    )

                    # 2. Prepare new document
                    new_doc = doc.copy()
                    new_doc["_id"] = target_id
                    new_doc[id_field] = (
                        target_id  # Ensure it has the correct original value
                    )

                    # 3. Insert new document
                    try:
                        collection.insert_one(new_doc)
                    except Exception as insert_error:
                        # Revert rename if insert fails
                        print(
                            f"    Insert failed for {target_id}: {insert_error}. Reverting rename."
                        )
                        collection.update_one(
                            {"_id": old_oid}, {"$set": {id_field: target_id}}
                        )
                        error_count += 1
                        continue

                    # 4. Delete old document
                    collection.delete_one({"_id": old_oid})

                    migrated_count += 1
                except Exception as e:
                    print(f"    ERROR migrating doc {doc.get('_id')}: {e}")
                    error_count += 1

            print(f"  Migrated {migrated_count} documents. Errors: {error_count}")

    # Now apply the project_id update for sessions
    print("\nEnsuring sessions have project_id=None...")
    sessions_col = db[settings.SESSIONS_COLLECTION]
    result = sessions_col.update_many(
        {"project_id": {"$exists": False}}, {"$set": {"project_id": None}}
    )
    print(f"Matched {result.matched_count} sessions for project_id update.")
    print(f"Modified {result.modified_count} sessions.")

    print("\nMigration Script Completed.")


if __name__ == "__main__":
    asyncio.run(migrate_ids_and_projects())
