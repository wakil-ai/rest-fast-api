"""Operator-facing subscription management.

Replaces hand-editing Mongo for users who paid outside the webhook flow. The
diagnostic route is the important one: it puts the stored document, the computed
entitlement, and the credit ledger side by side, which is what makes a
"subscription looks fine but the app says free" report answerable in one call.
"""

from fastapi import APIRouter, Depends, Query, Request

from core.dependencies import get_admin_audit_service, get_admin_subscription_service
from core.error_codes import ErrorCode
from core.exceptions import AdminActionError
from models.admin_subscription import (
    AdminAdjustCreditsRequest,
    AdminAuditListResponse,
    AdminExtendRequest,
    AdminGrantRequest,
    AdminRevokeRequest,
    AdminSubscriptionDiagnosticResponse,
    AdminSubscriptionMutationResponse,
)
from security import (
    get_admin_operator,
    get_admin_request_id,
    verify_super_admin_key,
)
from services.admin_audit_service import AdminActionAlreadyRecorded

router = APIRouter(
    prefix="/admin/subscriptions",
    tags=["Admin Subscriptions"],
    dependencies=[Depends(verify_super_admin_key)],
)


def _client_ip(request: Request) -> str | None:
    forwarded = request.headers.get("x-forwarded-for")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return request.client.host if request.client else None


async def _run(
    action: str,
    user_id: str,
    payload,
    request: Request,
    operator: str,
    request_id: str,
    service,
) -> dict:
    try:
        return await service.execute(
            action,
            user_id,
            payload,
            operator=operator,
            request_id=request_id,
            source_ip=_client_ip(request),
            user_agent=request.headers.get("user-agent"),
        )
    except AdminActionAlreadyRecorded as exc:
        raise AdminActionError(
            ErrorCode.ADMIN_ACTION_DUPLICATE,
            request_id=request_id,
            audit_id=exc.existing.get("event_id"),
            result=exc.existing.get("result"),
        ) from None


@router.get("/audit", response_model=AdminAuditListResponse)
async def list_admin_audit(
    user_id: str | None = None,
    operator: str | None = None,
    action: str | None = None,
    from_ms: int | None = None,
    to_ms: int | None = None,
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    audit_service=Depends(get_admin_audit_service),
):
    return await audit_service.list_entries(
        user_id=user_id,
        operator=operator,
        action=action,
        from_ms=from_ms,
        to_ms=to_ms,
        limit=limit,
        offset=offset,
    )


@router.get("/{user_id}", response_model=AdminSubscriptionDiagnosticResponse)
async def get_subscription_diagnostic(
    user_id: str,
    service=Depends(get_admin_subscription_service),
):
    """Stored vs. computed vs. ledger, with warnings. No side effects."""
    return await service.diagnostic(user_id)


@router.post("/{user_id}/grant", response_model=AdminSubscriptionMutationResponse)
async def grant_subscription(
    user_id: str,
    payload: AdminGrantRequest,
    request: Request,
    operator: str = Depends(get_admin_operator),
    request_id: str = Depends(get_admin_request_id),
    service=Depends(get_admin_subscription_service),
):
    return await _run("grant", user_id, payload, request, operator, request_id, service)


@router.post("/{user_id}/extend", response_model=AdminSubscriptionMutationResponse)
async def extend_subscription(
    user_id: str,
    payload: AdminExtendRequest,
    request: Request,
    operator: str = Depends(get_admin_operator),
    request_id: str = Depends(get_admin_request_id),
    service=Depends(get_admin_subscription_service),
):
    return await _run("extend", user_id, payload, request, operator, request_id, service)


@router.post(
    "/{user_id}/adjust-credits", response_model=AdminSubscriptionMutationResponse
)
async def adjust_subscription_credits(
    user_id: str,
    payload: AdminAdjustCreditsRequest,
    request: Request,
    operator: str = Depends(get_admin_operator),
    request_id: str = Depends(get_admin_request_id),
    service=Depends(get_admin_subscription_service),
):
    return await _run(
        "adjust_credits", user_id, payload, request, operator, request_id, service
    )


@router.post("/{user_id}/revoke", response_model=AdminSubscriptionMutationResponse)
async def revoke_subscription(
    user_id: str,
    payload: AdminRevokeRequest,
    request: Request,
    operator: str = Depends(get_admin_operator),
    request_id: str = Depends(get_admin_request_id),
    service=Depends(get_admin_subscription_service),
):
    return await _run("revoke", user_id, payload, request, operator, request_id, service)
