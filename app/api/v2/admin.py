import time
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pymongo import UpdateOne

from app.core.config import settings
from app.core.dependencies import get_mongo_handler
from app.core.logger import logger
from app.models.telegram import (
    TelegramChatEntry,
    TelegramChatsListResponse,
    TelegramChatsSaveRequest,
)
from app.security import verify_super_admin_key

router = APIRouter(prefix="/admin", tags=["Admin"])

mongo_handler = get_mongo_handler()


@router.post(
    "/telegram/save/chats",
    summary="Save Telegram chat ids (upsert)",
    dependencies=[Depends(verify_super_admin_key)],
)
async def save_telegram_chats(request: TelegramChatsSaveRequest):
    try:
        records = []
        if request.chat is not None:
            records.append(request.chat)
        if request.chats:
            records.extend(request.chats)

        if not records:
            raise HTTPException(status_code=400, detail="chat(s) required")

        # Deduplicate by (chat_id, user_id)
        dedup: dict[str, Any] = {}
        for r in records:
            key = f"{int(r.chat_id)}:{int(r.user_id)}"
            dedup[key] = r
        records = list(dedup.values())

        now_ms = int(time.time() * 1000)
        collection = mongo_handler.db[settings.TELEGRAM_CHATS_COLLECTION]

        # Best-effort: keep (chat_id, user_id) unique.
        try:
            await collection.create_index([("chat_id", 1), ("user_id", 1)], unique=True)
            await collection.create_index("chat_id")
            await collection.create_index("user_id")
        except Exception:
            pass

        ops: list[UpdateOne] = []
        for r in records:
            chat_id = int(r.chat_id)
            user_id = int(r.user_id)
            update_set: dict[str, Any] = {
                "chat_id": chat_id,
                "user_id": user_id,
                "is_active": True,
                "updated_at_ms": now_ms,
            }
            if getattr(r, "username", None):
                update_set["username"] = r.username
            if getattr(r, "first_name", None):
                update_set["first_name"] = r.first_name
            if getattr(r, "last_name", None):
                update_set["last_name"] = r.last_name
            if getattr(r, "language_code", None):
                update_set["language_code"] = r.language_code

            ops.append(
                UpdateOne(
                    {"chat_id": chat_id, "user_id": user_id},
                    {
                        "$set": update_set,
                        "$setOnInsert": {"created_at_ms": now_ms},
                    },
                    upsert=True,
                )
            )

        result = await collection.bulk_write(ops, ordered=False)

        return {
            "success": True,
            "received": len(records),
            "matched": int(getattr(result, "matched_count", 0) or 0),
            "modified": int(getattr(result, "modified_count", 0) or 0),
            "upserted": int(len(getattr(result, "upserted_ids", {}) or {})),
        }
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"[AdminAPI] Error saving telegram chats: {str(e)}")
        raise HTTPException(status_code=500, detail="Failed to save telegram chats")


@router.get(
    "/telegram/chats",
    response_model=TelegramChatsListResponse,
    summary="List Telegram chat ids",
    dependencies=[Depends(verify_super_admin_key)],
)
async def list_telegram_chats(active_only: bool = True):
    try:
        query = {"is_active": True} if active_only else {}
        projection = {
            "_id": 0,
            "chat_id": 1,
            "user_id": 1,
            "is_active": 1,
            "created_at_ms": 1,
            "updated_at_ms": 1,
        }
        collection = mongo_handler.db[settings.TELEGRAM_CHATS_COLLECTION]

        cursor = collection.find(query, projection).sort("updated_at_ms", -1)
        docs = await cursor.to_list(length=None)
        chats: list[TelegramChatEntry] = []
        for d in docs:
            if d.get("chat_id") is None or d.get("user_id") is None:
                continue
            chats.append(
                TelegramChatEntry(
                    chat_id=int(d["chat_id"]),
                    user_id=int(d["user_id"]),
                    is_active=bool(d.get("is_active", True)),
                    created_at_ms=(
                        int(d["created_at_ms"])
                        if d.get("created_at_ms") is not None
                        else None
                    ),
                    updated_at_ms=(
                        int(d["updated_at_ms"])
                        if d.get("updated_at_ms") is not None
                        else None
                    ),
                )
            )

        return TelegramChatsListResponse(chats=chats, count=len(chats))
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"[AdminAPI] Error listing telegram chats: {str(e)}")
        raise HTTPException(status_code=500, detail="Failed to list telegram chats")
