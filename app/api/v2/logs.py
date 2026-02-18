from enum import Enum
from typing import Any

from fastapi import APIRouter, Query

from app.core.dependencies import get_redis_service

router = APIRouter(prefix="/logs", tags=["Logs"])

redis_service = get_redis_service()


class LogLevel(str, Enum):
    debug = "debug"
    info = "info"
    success = "success"
    warning = "warning"
    error = "error"


@router.get(
    "/{level}",
    summary="Get logs by level",
    response_model=dict[str, Any],
)
async def get_logs_by_level(
    level: LogLevel,
    limit: int = Query(default=100, ge=1, le=1000, description="Max entries to return"),
):
    """
    Retrieve the latest log entries for a specific level.

    - **level**: one of `debug`, `info`, `success`, `warning`, `error`
    - **limit**: number of most-recent entries (1–1000, default 100)
    """
    logs = await redis_service.get_logs(level.value, limit=limit)
    return {"level": level.value, "count": len(logs), "logs": logs}


@router.get(
    "",
    summary="Get all logs",
    response_model=dict[str, Any],
)
async def get_all_logs(
    limit: int = Query(default=50, ge=1, le=500, description="Max entries per level"),
):
    """Retrieve the latest log entries across every level."""
    result: dict[str, Any] = {}
    for lvl in LogLevel:
        entries = await redis_service.get_logs(lvl.value, limit=limit)
        result[lvl.value] = {"count": len(entries), "logs": entries}
    return result


@router.delete(
    "/{level}",
    summary="Clear logs by level",
)
async def clear_logs_by_level(level: LogLevel):
    """Delete all stored log entries for a given level."""
    removed = await redis_service.clear_logs(level.value)
    return {"level": level.value, "removed": removed}
