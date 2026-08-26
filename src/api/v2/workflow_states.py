"""Workflow states: the board columns an organization defines for Cases and Tasks."""

from typing import Any

from fastapi import APIRouter, Depends, status

from core.dependencies import get_workflow_state_service
from models.workflow_states import (
    StateAppliesTo,
    WorkflowStateCreateRequest,
    WorkflowStateListResponse,
    WorkflowStateResponse,
    WorkflowStateUpdateRequest,
)
from security import get_current_user_id
from utils.user_management import handle_service_error

router = APIRouter(prefix="/organizations", tags=["Workflow States"])

# Every route takes its acting user from the verified JWT subject. There is no
# user_id parameter to spoof, and no service-key path onto this router.
CurrentUser = Depends(get_current_user_id)

state_service = get_workflow_state_service()


def _to_response(doc: dict[str, Any]) -> WorkflowStateResponse:
    return WorkflowStateResponse(
        state_id=str(doc["_id"]),
        org_id=doc["org_id"],
        name=doc["name"],
        category=doc["category"],
        applies_to=doc["applies_to"],
        order=doc.get("order", 0),
        is_system=bool(doc.get("is_system")),
    )


@router.get("/{org_id}/workflow-states", response_model=WorkflowStateListResponse)
@handle_service_error
async def list_workflow_states(
    org_id: str,
    applies_to: StateAppliesTo | None = None,
    user_id: str = CurrentUser,
):
    """The organization's boards. Any active member may read them.

    Omit ``applies_to`` for both boards. An organization that predates this
    feature is seeded on its first read, so the list is never empty.
    """
    docs = await state_service.list_states(org_id, user_id, applies_to=applies_to)
    return WorkflowStateListResponse(
        org_id=org_id, states=[_to_response(d) for d in docs]
    )


@router.post(
    "/{org_id}/workflow-states",
    response_model=WorkflowStateResponse,
    status_code=status.HTTP_201_CREATED,
)
@handle_service_error
async def create_workflow_state(
    org_id: str, body: WorkflowStateCreateRequest, user_id: str = CurrentUser
):
    """Add a column to a board. Admin only — the board is shared by everyone."""
    doc = await state_service.create_state(
        org_id, user_id, body.model_dump(exclude_unset=True)
    )
    return _to_response(doc)


@router.patch(
    "/{org_id}/workflow-states/{state_id}", response_model=WorkflowStateResponse
)
@handle_service_error
async def update_workflow_state(
    org_id: str,
    state_id: str,
    body: WorkflowStateUpdateRequest,
    user_id: str = CurrentUser,
):
    """Rename or reposition a column. Admin only.

    Default states may be renamed here; only deletion is refused.
    """
    doc = await state_service.update_state(
        org_id, state_id, user_id, body.model_dump(exclude_unset=True)
    )
    return _to_response(doc)


@router.delete(
    "/{org_id}/workflow-states/{state_id}", status_code=status.HTTP_204_NO_CONTENT
)
@handle_service_error
async def delete_workflow_state(org_id: str, state_id: str, user_id: str = CurrentUser):
    """Archive a custom column. Admin only; default states are refused."""
    await state_service.archive_state(org_id, state_id, user_id)
