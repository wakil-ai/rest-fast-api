"""Cases: the enterprise unit of work (project), scoped to an organization."""

from typing import Any

from fastapi import APIRouter, Depends, Query, status

from core.dependencies import get_case_service, get_draft_service
from models.cases import (
    CaseClosure,
    CaseCreateRequest,
    CaseListResponse,
    CaseResponse,
    CaseUpdateRequest,
)
from models.drafts import (
    DelegatePrepareRequest,
    DelegatePrepareResponse,
    DelegateRequest,
)
from models.projects import ProjectStatus
from security import get_current_user_id
from utils.user_management import handle_service_error

router = APIRouter(prefix="/organizations", tags=["Cases"])

# Every route takes its acting user from the verified JWT subject. There is no
# user_id parameter to spoof, and no service-key path onto this router.
CurrentUser = Depends(get_current_user_id)

case_service = get_case_service()


def _to_response(doc: dict[str, Any]) -> CaseResponse:
    # `_id` is a proj- string: a Case is a projects row, and the whole file and
    # vector-search pipeline is keyed on it. The rename is presentation only.
    return CaseResponse(
        case_id=str(doc["_id"]),
        org_id=doc["org_id"],
        owner_id=doc["owner_id"],
        title=doc["title"],
        description=doc.get("description"),
        objective=doc.get("objective"),
        case_type=doc.get("case_type"),
        state_id=doc.get("state_id"),
        start_date=doc.get("start_date"),
        deadline=doc.get("deadline"),
        assignee_id=doc.get("assignee_id"),
        suspect=doc.get("suspect"),
        victim=doc.get("victim"),
        status=doc.get("status", ProjectStatus.active.value),
        closure=CaseClosure(**(doc.get("closure") or {})),
        files=doc.get("files") or [],
        metadata=doc.get("metadata") or {},
        stats=doc.get("stats") or {},
        updated_by=doc.get("updated_by"),
        created_at=doc["created_at"],
        updated_at=doc["updated_at"],
    )


@router.post(
    "/{org_id}/cases", response_model=CaseResponse, status_code=status.HTTP_201_CREATED
)
@handle_service_error
async def create_case(org_id: str, body: CaseCreateRequest, user_id: str = CurrentUser):
    """Open a Case. Any active member of the organization may do this."""
    doc = await case_service.create_case(
        org_id, user_id, body.model_dump(exclude_unset=True)
    )
    return _to_response(doc)


@router.get("/{org_id}/cases", response_model=CaseListResponse)
@handle_service_error
async def list_cases(
    org_id: str,
    state_id: str | None = None,
    assignee_id: str | None = None,
    case_status: ProjectStatus | None = None,
    limit: int = Query(default=50, ge=1, le=200),
    skip: int = Query(default=0, ge=0),
    user_id: str = CurrentUser,
):
    """The organization's Cases. Members see all of them — the spec gives the
    Head oversight of the department, and per-Case access control is a separate
    paid feature."""
    docs = await case_service.list_cases(
        org_id,
        user_id,
        state_id=state_id,
        assignee_id=assignee_id,
        case_status=case_status,
        limit=limit,
        skip=skip,
    )
    return CaseListResponse(org_id=org_id, cases=[_to_response(d) for d in docs])


@router.get("/{org_id}/cases/{case_id}", response_model=CaseResponse)
@handle_service_error
async def get_case(org_id: str, case_id: str, user_id: str = CurrentUser):
    return _to_response(await case_service.get_case(org_id, case_id, user_id))


@router.patch("/{org_id}/cases/{case_id}", response_model=CaseResponse)
@handle_service_error
async def update_case(
    org_id: str, case_id: str, body: CaseUpdateRequest, user_id: str = CurrentUser
):
    """Edit a Case. Its owner, its assignee, or an admin.

    `status` and `closure` are not editable here — see the closure endpoints.
    """
    doc = await case_service.update_case(
        org_id, case_id, user_id, body.model_dump(exclude_unset=True)
    )
    return _to_response(doc)


@router.delete("/{org_id}/cases/{case_id}", status_code=status.HTTP_204_NO_CONTENT)
@handle_service_error
async def archive_case(org_id: str, case_id: str, user_id: str = CurrentUser):
    """Archive a Case. Admin only. Soft delete — nothing is removed."""
    await case_service.archive_case(org_id, case_id, user_id)


@router.post("/{org_id}/cases/{case_id}/closure/request", response_model=CaseResponse)
@handle_service_error
async def request_case_closure(org_id: str, case_id: str, user_id: str = CurrentUser):
    """Ask the Head to sign the Case off. Owner, assignee, or admin."""
    doc = await case_service.request_closure(org_id, case_id, user_id)
    return _to_response(doc)


@router.post("/{org_id}/cases/{case_id}/closure/approve", response_model=CaseResponse)
@handle_service_error
async def approve_case_closure(org_id: str, case_id: str, user_id: str = CurrentUser):
    """Confirm the Case is fully closed. Admin only, per the spec's §3.5 oversight."""
    doc = await case_service.approve_closure(org_id, case_id, user_id)
    return _to_response(doc)


@router.post("/{org_id}/cases/{case_id}/closure/reopen", response_model=CaseResponse)
@handle_service_error
async def reopen_case(org_id: str, case_id: str, user_id: str = CurrentUser):
    """Undo a closure and put the Case back on the draft column. Admin only.

    Without this a Case closed by mistake is a dead end: its board position is
    frozen and its closure cannot be approved twice.
    """
    doc = await case_service.reopen(org_id, case_id, user_id)
    return _to_response(doc)


@router.post(
    "/{org_id}/cases/{case_id}/delegate/prepare",
    response_model=DelegatePrepareResponse,
)
@handle_service_error
async def prepare_case_delegation(
    org_id: str,
    case_id: str,
    body: DelegatePrepareRequest | None = None,
    user_id: str = CurrentUser,
):
    """Compose the request for the employee to review before anything is spent.

    Registered before `/delegate` is unnecessary here — the paths differ by a
    literal tail, not a parameter — but the pairing matters: nothing in this
    route reserves a slot or charges credits, so opening the review step and
    abandoning it leaves no trace to clean up.
    """
    body = body or DelegatePrepareRequest()
    return await get_draft_service().prepare_delegation(
        org_id=org_id,
        case_id=case_id,
        task_id=None,
        user_id=user_id,
        previous_draft_id=body.previous_draft_id,
        instruction=body.instruction,
        language=body.language,
    )


@router.post("/{org_id}/cases/{case_id}/delegate")
@handle_service_error
async def delegate_case(
    org_id: str,
    case_id: str,
    body: DelegateRequest,
    user_id: str = CurrentUser,
):
    """Hand this Case to its agent. The answer is a draft until a human confirms.

    Streams SSE. Everything that can refuse — membership, a slot already held,
    credits — runs before the first byte, so a refusal is ordinary JSON.
    """
    return await get_draft_service().delegate(
        org_id=org_id,
        case_id=case_id,
        task_id=None,
        user_id=user_id,
        instruction=body.instruction,
        query=body.query,
        assistant=body.assistant,
        base_hash=body.base_hash,
        language=body.language,
    )
