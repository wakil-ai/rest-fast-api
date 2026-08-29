"""The gate's HTTP surface: read a draft, see its history, decide on it.

JWT only, per-route. There is deliberately no service-key path onto this router:
a machine must never be able to approve a draft, which is the whole point.
"""

from typing import Any

from fastapi import APIRouter, Depends, Response

from core.dependencies import get_draft_service
from models.drafts import DraftEditRequest, DraftListResponse, DraftResponse
from security import get_current_user_id
from utils.message_export import DOCX_CONTENT_TYPE
from utils.user_management import handle_service_error

router = APIRouter(prefix="/organizations", tags=["Drafts"])

# Every route takes its acting user from the verified JWT subject. There is no
# user_id parameter to spoof, and no service-key path onto this router.
CurrentUser = Depends(get_current_user_id)

service = get_draft_service()


def _to_response(doc: dict[str, Any]) -> DraftResponse:
    return DraftResponse(
        draft_id=str(doc["_id"]),
        org_id=doc["org_id"],
        case_id=doc["case_id"],
        task_id=doc.get("task_id"),
        session_id=doc["session_id"],
        message_id=doc.get("message_id"),
        chain_id=doc["chain_id"],
        parent_draft_id=doc.get("parent_draft_id"),
        version=doc["version"],
        source=doc["source"],
        status=doc["status"],
        assistant=doc["assistant"],
        created_by=doc["created_by"],
        decided_by=doc.get("decided_by"),
        decided_at=doc.get("decided_at"),
        edited=doc.get("edited", False),
        created_at=doc["created_at"],
        # Absent on list endpoints, which project it away.
        content=doc.get("content"),
        query_base=doc.get("query_base"),
        query_final=doc.get("query_final"),
        query_edited=doc.get("query_edited", False),
    )


@router.get("/{org_id}/drafts/{draft_id}", response_model=DraftResponse)
@handle_service_error
async def get_draft(org_id: str, draft_id: str, user_id: str = CurrentUser):
    """One draft, including its text."""
    row = await service.assert_draft_access(org_id, draft_id, user_id)
    return _to_response(row)


@router.get("/{org_id}/drafts/{draft_id}/chain", response_model=DraftListResponse)
@handle_service_error
async def get_draft_chain(org_id: str, draft_id: str, user_id: str = CurrentUser):
    """Every version, oldest first. Version 1 is the machine's untouched text."""
    rows = await service.get_chain(org_id, draft_id, user_id)
    return DraftListResponse(drafts=[_to_response(r) for r in rows])


@router.get("/{org_id}/cases/{case_id}/drafts", response_model=DraftListResponse)
@handle_service_error
async def list_case_drafts(org_id: str, case_id: str, user_id: str = CurrentUser):
    rows = await service.list_for_case(org_id, case_id, user_id)
    return DraftListResponse(drafts=[_to_response(r) for r in rows])


@router.get("/{org_id}/tasks/{task_id}/drafts", response_model=DraftListResponse)
@handle_service_error
async def list_task_drafts(org_id: str, task_id: str, user_id: str = CurrentUser):
    rows = await service.list_for_task(org_id, task_id, user_id)
    return DraftListResponse(drafts=[_to_response(r) for r in rows])


@router.post("/{org_id}/drafts/{draft_id}/edit", response_model=DraftResponse)
@handle_service_error
async def edit_draft(
    org_id: str,
    draft_id: str,
    body: DraftEditRequest,
    user_id: str = CurrentUser,
):
    """Rewrite a draft. Creates a new version; the old one is superseded."""
    row = await service.edit(org_id, draft_id, user_id, body.content)
    return _to_response(row)


@router.post("/{org_id}/drafts/{draft_id}/approve", response_model=DraftResponse)
@handle_service_error
async def approve_draft(org_id: str, draft_id: str, user_id: str = CurrentUser):
    """Confirm a draft. This is the act that gives it legal effect."""
    row = await service.approve(org_id, draft_id, user_id)
    return _to_response(row)


@router.post("/{org_id}/drafts/{draft_id}/reject", response_model=DraftResponse)
@handle_service_error
async def reject_draft(org_id: str, draft_id: str, user_id: str = CurrentUser):
    """Refuse a draft. Terminal — delegating again starts a new chain."""
    row = await service.reject(org_id, draft_id, user_id)
    return _to_response(row)


@router.get("/{org_id}/drafts/{draft_id}/docx")
@handle_service_error
async def download_draft_docx(org_id: str, draft_id: str, user_id: str = CurrentUser):
    """Approved draft as a .docx.

    Rendered from the draft row's own text. The message-based export next door
    would return the machine's original wording for any draft a human corrected,
    because an edit stores no message id on the new version.
    """
    content, filename = await service.render_docx(org_id, draft_id, user_id)
    return Response(
        content=content,
        media_type=DOCX_CONTENT_TYPE,
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )
