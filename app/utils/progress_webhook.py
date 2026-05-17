from typing import Any

import httpx

from app.core.logger import logger


async def send_project_file_progress_webhook(
    webhook_url: str,
    *,
    project_id: str,
    file_id: str,
    stage: str,
    progress_percent: int,
    status: str,
    extra: dict[str, Any] | None = None,
) -> None:
    """POST processing progress to a client-controlled URL (best-effort)."""
    if not webhook_url or not webhook_url.strip():
        return

    payload: dict[str, Any] = {
        "event": "project_file.processing",
        "project_id": project_id,
        "file_id": file_id,
        "stage": stage,
        "progress_percent": progress_percent,
        "status": status,
    }
    if extra:
        payload["extra"] = extra

    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            r = await client.post(webhook_url.strip(), json=payload)
            r.raise_for_status()
    except Exception as exc:
        logger.warning(
            f"Project file webhook failed url={webhook_url!r} file_id={file_id}: {exc}"
        )
