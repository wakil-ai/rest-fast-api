"""Project collaboration: members and invitation links."""

from fastapi import APIRouter, status

from core.dependencies import get_project_member_service, get_project_service
from models.project_collaboration import (
    ProjectInviteAcceptRequest,
    ProjectInviteAcceptResponse,
    ProjectInvitePreviewResponse,
    ProjectInviteResponse,
    ProjectInviteStatus,
    ProjectMemberResponse,
    ProjectMembersListResponse,
    ProjectMembershipRole,
)
from utils.user_management import handle_service_error, serialize_mongo_id

router = APIRouter(prefix="/projects", tags=["Project Collaboration"])

project_service = get_project_service()
member_service = get_project_member_service()


def _invite_to_response(doc: dict) -> ProjectInviteResponse:
    d = serialize_mongo_id(doc)
    invite_id = str(d.get("_id", ""))
    return ProjectInviteResponse(
        invite_id=invite_id,
        project_id=d["project_id"],
        status=ProjectInviteStatus(d.get("status", ProjectInviteStatus.pending.value)),
        expires_at=d["expires_at"],
        created_at=d["created_at"],
        join_path=d.get("join_path") or member_service._invite_join_path(invite_id),
    )


@router.get(
    "/invites/{invite_id}",
    response_model=ProjectInvitePreviewResponse,
)
@handle_service_error
async def preview_project_invite(invite_id: str, user_id: str):
    preview = await member_service.get_invite_preview(invite_id, user_id)
    return ProjectInvitePreviewResponse.model_validate(preview)


@router.post(
    "/invites/{invite_id}/accept",
    response_model=ProjectInviteAcceptResponse,
)
@handle_service_error
async def accept_project_invite(invite_id: str, body: ProjectInviteAcceptRequest):
    result = await member_service.accept_invite(invite_id, body.user_id)
    return ProjectInviteAcceptResponse.model_validate(result)


@router.get(
    "/{project_id}/members",
    response_model=ProjectMembersListResponse,
)
@handle_service_error
async def list_project_members(project_id: str, user_id: str):
    project = await project_service.assert_project_access(project_id, user_id)
    members = await member_service.list_members(project_id, project)
    return ProjectMembersListResponse(
        project_id=project_id,
        members=[ProjectMemberResponse.model_validate(m) for m in members],
    )


@router.delete(
    "/{project_id}/members/{member_user_id}",
    status_code=status.HTTP_204_NO_CONTENT,
)
@handle_service_error
async def remove_project_member(
    project_id: str, member_user_id: str, user_id: str
):
    await member_service.remove_member(project_id, member_user_id, user_id)


@router.post(
    "/{project_id}/invites",
    status_code=status.HTTP_201_CREATED,
    response_model=ProjectInviteResponse,
)
@handle_service_error
async def create_project_invite(project_id: str, user_id: str):
    """Create invite link. ``user_id`` must be the logged-in project owner (not a member)."""
    doc = await member_service.create_invite(project_id, user_id)
    return _invite_to_response(doc)


@router.get(
    "/{project_id}/invites",
    response_model=list[ProjectInviteResponse],
)
@handle_service_error
async def list_project_invites(project_id: str, user_id: str):
    rows = await member_service.list_pending_invites(project_id, user_id)
    return [_invite_to_response(r) for r in rows]


@router.delete(
    "/{project_id}/invites/{invite_id}",
    status_code=status.HTTP_204_NO_CONTENT,
)
@handle_service_error
async def revoke_project_invite(project_id: str, invite_id: str, user_id: str):
    await member_service.revoke_invite(project_id, invite_id, user_id)
