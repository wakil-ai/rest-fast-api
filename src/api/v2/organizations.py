"""Organizations: workspace creation, membership, and one-time invite links."""

from typing import Any

from fastapi import APIRouter, Depends, status

from core.dependencies import (
    get_organization_member_service,
    get_organization_service,
)
from models.organizations import (
    OrganizationCreateRequest,
    OrganizationInviteAcceptResponse,
    OrganizationInviteListResponse,
    OrganizationInvitePreviewResponse,
    OrganizationInviteResponse,
    OrganizationListResponse,
    OrganizationMemberResponse,
    OrganizationMembersListResponse,
    OrganizationResponse,
)
from security import get_current_user_id
from utils.user_management import handle_service_error

router = APIRouter(prefix="/organizations", tags=["Organizations"])

# Every route takes its acting user from the verified JWT subject. There is no
# user_id parameter to spoof, and no service-key path onto this router.
CurrentUser = Depends(get_current_user_id)

org_service = get_organization_service()
member_service = get_organization_member_service()


def _org_to_response(doc: dict[str, Any]) -> OrganizationResponse:
    return OrganizationResponse(
        org_id=str(doc["_id"]),
        name=doc["name"],
        icon=doc.get("icon"),
        created_by=doc["created_by"],
        status=doc["status"],
        seat_limit=doc["seat_limit"],
        settings=doc.get("settings") or {},
        created_at=doc["created_at"],
        updated_at=doc["updated_at"],
        role=doc["role"],
        member_count=doc.get("member_count", 0),
    )


@router.post(
    "",
    response_model=OrganizationResponse,
    status_code=status.HTTP_201_CREATED,
)
@handle_service_error
async def create_organization(
    body: OrganizationCreateRequest, user_id: str = CurrentUser
):
    doc = await org_service.create_organization(
        user_id=user_id,
        name=body.name,
        icon=body.icon,
        settings_obj=body.settings,
    )
    return _org_to_response(doc)


@router.get("", response_model=OrganizationListResponse)
@handle_service_error
async def list_organizations(user_id: str = CurrentUser):
    docs = await org_service.list_user_organizations(user_id)
    return OrganizationListResponse(organizations=[_org_to_response(d) for d in docs])


def _invite_to_response(doc: dict[str, Any]) -> OrganizationInviteResponse:
    invite_id = str(doc["_id"])
    return OrganizationInviteResponse(
        invite_id=invite_id,
        org_id=doc["org_id"],
        status=doc["status"],
        created_by=doc["created_by"],
        expires_at=doc["expires_at"],
        created_at=doc["created_at"],
        join_path=doc.get("join_path") or member_service.invite_join_path(invite_id),
    )


@router.post(
    "/{org_id}/invites",
    response_model=OrganizationInviteResponse,
    status_code=status.HTTP_201_CREATED,
)
@handle_service_error
async def create_organization_invite(org_id: str, user_id: str = CurrentUser):
    doc = await member_service.create_invite(org_id, user_id)
    return _invite_to_response(doc)


@router.get("/{org_id}/invites", response_model=OrganizationInviteListResponse)
@handle_service_error
async def list_organization_invites(org_id: str, user_id: str = CurrentUser):
    docs = await member_service.list_pending_invites(org_id, user_id)
    return OrganizationInviteListResponse(
        org_id=org_id, invites=[_invite_to_response(d) for d in docs]
    )


@router.delete("/{org_id}/invites/{invite_id}", status_code=status.HTTP_204_NO_CONTENT)
@handle_service_error
async def revoke_organization_invite(
    org_id: str, invite_id: str, user_id: str = CurrentUser
):
    await member_service.revoke_invite(org_id, invite_id, user_id)


@router.get("/invites/{invite_id}", response_model=OrganizationInvitePreviewResponse)
@handle_service_error
async def preview_organization_invite(invite_id: str, user_id: str = CurrentUser):
    preview = await member_service.get_invite_preview(invite_id, user_id)
    return OrganizationInvitePreviewResponse.model_validate(preview)


@router.post(
    "/invites/{invite_id}/accept",
    response_model=OrganizationInviteAcceptResponse,
)
@handle_service_error
async def accept_organization_invite(invite_id: str, user_id: str = CurrentUser):
    result = await member_service.accept_invite(invite_id, user_id)
    return OrganizationInviteAcceptResponse.model_validate(result)


@router.get("/{org_id}/members", response_model=OrganizationMembersListResponse)
@handle_service_error
async def list_organization_members(org_id: str, user_id: str = CurrentUser):
    # No membership assertion here: list_members already calls assert_org_member,
    # and duplicating it costs a second round trip for the same answer.
    members = await member_service.list_members(org_id, user_id)
    return OrganizationMembersListResponse(
        org_id=org_id,
        members=[OrganizationMemberResponse.model_validate(m) for m in members],
    )
