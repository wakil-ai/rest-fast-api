"""Activity logs: the organization's immutable timeline.

Read-only by design. There is no POST, PATCH or DELETE here — events are written
by the services that perform the actions, and nothing edits them afterwards.
"""

from typing import Any

from fastapi import APIRouter, Depends

from core.dependencies import get_activity_log_service
from models.activity_logs import (
    ActivityActor,
    ActivityLogListResponse,
    ActivityLogResponse,
    ActivityObject,
)
from security import get_current_user_id
from utils.user_management import handle_service_error

router = APIRouter(prefix="/organizations", tags=["Activity Logs"])

# Every route takes its acting user from the verified JWT subject. There is no
# user_id parameter to spoof, and no service-key path onto this router.
CurrentUser = Depends(get_current_user_id)

log_service = get_activity_log_service()


def _to_response(doc: dict[str, Any]) -> ActivityLogResponse:
    return ActivityLogResponse(
        log_id=str(doc["_id"]),
        org_id=doc["org_id"],
        occurred_at=doc["occurred_at"],
        event_type=doc["event_type"],
        actor=ActivityActor(**(doc.get("actor") or {})),
        on_behalf_of=doc.get("on_behalf_of"),
        object=ActivityObject(**(doc.get("object") or {})),
        case_id=doc.get("case_id"),
        task_id=doc.get("task_id"),
        payload=doc.get("payload") or {},
    )


@router.get("/{org_id}/activity-logs", response_model=ActivityLogListResponse)
@handle_service_error
async def list_activity_logs(
    org_id: str,
    case_id: str | None = None,
    task_id: str | None = None,
    limit: int = 50,
    before: str | None = None,
    user_id: str = CurrentUser,
):
    """Newest first. Any active member may read their organization's timeline.

    `before` is the `next_before` from the previous page — log ids are uuid7, so
    they sort chronologically and double as the cursor.
    """
    limit = max(1, min(limit, 200))
    docs = await log_service.list_logs(
        org_id, user_id, case_id=case_id, task_id=task_id, limit=limit, before=before
    )
    logs = [_to_response(d) for d in docs]
    return ActivityLogListResponse(
        org_id=org_id,
        logs=logs,
        # Only offer a cursor when the page was full; a short page is the end.
        next_before=logs[-1].log_id if len(logs) == limit else None,
    )


@router.get("/{org_id}/activity-logs/{log_id}", response_model=ActivityLogResponse)
@handle_service_error
async def get_activity_log(org_id: str, log_id: str, user_id: str = CurrentUser):
    return _to_response(await log_service.get_log(org_id, log_id, user_id))
