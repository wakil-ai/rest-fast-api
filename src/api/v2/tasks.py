"""Tasks: assignments within a Case."""

from typing import Any

from fastapi import APIRouter, Depends, Query, status

from core.dependencies import get_draft_service, get_task_service
from models.cases import CaseClosure
from models.drafts import (
    DelegatePrepareRequest,
    DelegatePrepareResponse,
    DelegateRequest,
)
from models.tasks import (
    TaskCreateRequest,
    TaskListResponse,
    TaskResponse,
    TaskUpdateRequest,
)
from security import get_current_user_id
from utils.user_management import handle_service_error

router = APIRouter(prefix="/organizations", tags=["Tasks"])

# Every route takes its acting user from the verified JWT subject. There is no
# user_id parameter to spoof, and no service-key path onto this router.
CurrentUser = Depends(get_current_user_id)

task_service = get_task_service()


def _to_response(doc: dict[str, Any]) -> TaskResponse:
    return TaskResponse(
        task_id=str(doc["_id"]),
        case_id=doc["case_id"],
        org_id=doc["org_id"],
        title=doc["title"],
        description=doc.get("description"),
        objective=doc.get("objective"),
        task_type=doc.get("task_type"),
        state_id=doc.get("state_id"),
        start_date=doc.get("start_date"),
        deadline=doc.get("deadline"),
        assignee_id=doc.get("assignee_id"),
        ai_brief=doc.get("ai_brief"),
        closure=CaseClosure(**(doc.get("closure") or {})),
        created_by=doc["created_by"],
        updated_by=doc.get("updated_by"),
        created_at=doc["created_at"],
        updated_at=doc["updated_at"],
    )


@router.post(
    "/{org_id}/cases/{case_id}/tasks",
    response_model=TaskResponse,
    status_code=status.HTTP_201_CREATED,
)
@handle_service_error
async def create_task(
    org_id: str, case_id: str, body: TaskCreateRequest, user_id: str = CurrentUser
):
    """Add a Task to a Case. Any active member of the organization."""
    doc = await task_service.create_task(
        org_id, case_id, user_id, body.model_dump(exclude_unset=True)
    )
    return _to_response(doc)


@router.get("/{org_id}/cases/{case_id}/tasks", response_model=TaskListResponse)
@handle_service_error
async def list_case_tasks(
    org_id: str,
    case_id: str,
    assignee_id: str | None = None,
    state_id: str | None = None,
    limit: int = Query(default=100, ge=1, le=200),
    skip: int = Query(default=0, ge=0),
    user_id: str = CurrentUser,
):
    """A Case's Tasks. Paged like the org-wide list — without `skip` a Case with
    more than `limit` Tasks would hide the rest with no way to reach them."""
    docs = await task_service.list_tasks(
        org_id,
        user_id,
        case_id=case_id,
        assignee_id=assignee_id,
        state_id=state_id,
        limit=limit,
        skip=skip,
    )
    return TaskListResponse(
        org_id=org_id, case_id=case_id, tasks=[_to_response(d) for d in docs]
    )


@router.get("/{org_id}/tasks", response_model=TaskListResponse)
@handle_service_error
async def list_org_tasks(
    org_id: str,
    assignee_id: str | None = None,
    state_id: str | None = None,
    limit: int = Query(default=100, ge=1, le=200),
    skip: int = Query(default=0, ge=0),
    user_id: str = CurrentUser,
):
    """Every Task in the organization. `assignee_id` gives the "my work" view."""
    docs = await task_service.list_tasks(
        org_id,
        user_id,
        assignee_id=assignee_id,
        state_id=state_id,
        limit=limit,
        skip=skip,
    )
    return TaskListResponse(org_id=org_id, tasks=[_to_response(d) for d in docs])


@router.get("/{org_id}/tasks/{task_id}", response_model=TaskResponse)
@handle_service_error
async def get_task(org_id: str, task_id: str, user_id: str = CurrentUser):
    return _to_response(await task_service.get_task(org_id, task_id, user_id))


@router.patch("/{org_id}/tasks/{task_id}", response_model=TaskResponse)
@handle_service_error
async def update_task(
    org_id: str, task_id: str, body: TaskUpdateRequest, user_id: str = CurrentUser
):
    """Edit a Task. Its creator, its assignee, or an admin."""
    doc = await task_service.update_task(
        org_id, task_id, user_id, body.model_dump(exclude_unset=True)
    )
    return _to_response(doc)


@router.delete("/{org_id}/tasks/{task_id}", status_code=status.HTTP_204_NO_CONTENT)
@handle_service_error
async def archive_task(org_id: str, task_id: str, user_id: str = CurrentUser):
    """Archive a Task. Admin only. Soft delete — nothing is removed."""
    await task_service.archive_task(org_id, task_id, user_id)


@router.post("/{org_id}/tasks/{task_id}/closure/request", response_model=TaskResponse)
@handle_service_error
async def request_task_closure(org_id: str, task_id: str, user_id: str = CurrentUser):
    """Mark the Task ready for sign-off. Creator, assignee, or admin."""
    return _to_response(await task_service.request_closure(org_id, task_id, user_id))


@router.post("/{org_id}/tasks/{task_id}/closure/approve", response_model=TaskResponse)
@handle_service_error
async def approve_task_closure(org_id: str, task_id: str, user_id: str = CurrentUser):
    """Confirm the Task is complete. Admin only."""
    return _to_response(await task_service.approve_closure(org_id, task_id, user_id))


@router.post("/{org_id}/tasks/{task_id}/closure/reopen", response_model=TaskResponse)
@handle_service_error
async def reopen_task(org_id: str, task_id: str, user_id: str = CurrentUser):
    """Undo a closure and put the Task back on the draft column. Admin only."""
    return _to_response(await task_service.reopen(org_id, task_id, user_id))


@router.post(
    "/{org_id}/tasks/{task_id}/delegate/prepare",
    response_model=DelegatePrepareResponse,
)
@handle_service_error
async def prepare_task_delegation(
    org_id: str,
    task_id: str,
    body: DelegatePrepareRequest | None = None,
    user_id: str = CurrentUser,
):
    """Compose the request for the employee to review before anything is spent."""
    body = body or DelegatePrepareRequest()
    return await get_draft_service().prepare_delegation(
        org_id=org_id,
        case_id="",
        task_id=task_id,
        user_id=user_id,
        previous_draft_id=body.previous_draft_id,
        instruction=body.instruction,
        language=body.language,
    )


@router.post("/{org_id}/tasks/{task_id}/delegate")
@handle_service_error
async def delegate_task(
    org_id: str,
    task_id: str,
    body: DelegateRequest,
    user_id: str = CurrentUser,
):
    """Hand this Task to its agent. The answer is a draft until a human confirms.

    `case_id` is resolved from the Task row inside delegate(), never taken from
    the caller — the two can then never disagree.
    """
    return await get_draft_service().delegate(
        org_id=org_id,
        case_id="",
        task_id=task_id,
        user_id=user_id,
        instruction=body.instruction,
        query=body.query,
        assistant=body.assistant,
        base_hash=body.base_hash,
        language=body.language,
    )
