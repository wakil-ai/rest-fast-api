from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status

from app.core.dependencies import get_fingerprint_service
from app.core.logger import logger
from app.models.fingerprint import (
    FingerprintBlockRequest,
    FingerprintBlockResponse,
    FingerprintCollectRequest,
    FingerprintCollectResponse,
    FingerprintUserLink,
    MultiAccountFingerprint,
    MultiAccountFingerprintsResponse,
    UserFingerprintsResponse,
    VisitorFingerprintDetailResponse,
)
from app.security import verify_api_key_or_dt_key, verify_super_admin_key
from app.services.fingerprint_service import FingerprintService

router = APIRouter(prefix="/fingerprint", tags=["Fingerprint"])


def _client_ip(request: Request) -> str | None:
    forwarded = request.headers.get("x-forwarded-for")
    if forwarded:
        return forwarded.split(",")[0].strip()
    if request.client:
        return request.client.host
    return None


@router.post(
    "/collect",
    response_model=FingerprintCollectResponse,
    summary="Record a device fingerprint for a user session",
    dependencies=[Depends(verify_api_key_or_dt_key)],
)
async def collect_fingerprint(
    body: FingerprintCollectRequest,
    request: Request,
    fingerprint_service: FingerprintService = Depends(get_fingerprint_service),
) -> FingerprintCollectResponse:
    try:
        result = await fingerprint_service.collect(
            user_id=body.user_id,
            visitor_id=body.visitor_id,
            metadata=body.metadata,
            ip_address=_client_ip(request),
            user_agent=request.headers.get("user-agent"),
        )
        return FingerprintCollectResponse(**result)
    except Exception as exc:
        logger.error(f"[FingerprintAPI] collect failed: {exc}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to store fingerprint",
        ) from exc


@router.get(
    "/multi-account",
    response_model=MultiAccountFingerprintsResponse,
    summary="List devices shared by multiple user accounts",
    dependencies=[Depends(verify_super_admin_key)],
)
async def list_multi_account_fingerprints(
    min_accounts: Annotated[int, Query(ge=2, le=100)] = 2,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    skip: Annotated[int, Query(ge=0)] = 0,
    include_blocked: bool = True,
    fingerprint_service: FingerprintService = Depends(get_fingerprint_service),
) -> MultiAccountFingerprintsResponse:
    items, total = await fingerprint_service.list_multi_account(
        min_accounts=min_accounts,
        limit=limit,
        skip=skip,
        include_blocked=include_blocked,
    )
    return MultiAccountFingerprintsResponse(
        total=total,
        items=[MultiAccountFingerprint(**item) for item in items],
    )


@router.get(
    "/user/{user_id}",
    response_model=UserFingerprintsResponse,
    summary="List fingerprints associated with a user",
    dependencies=[Depends(verify_super_admin_key)],
)
async def get_user_fingerprints(
    user_id: str,
    limit: Annotated[int, Query(ge=1, le=500)] = 100,
    fingerprint_service: FingerprintService = Depends(get_fingerprint_service),
) -> UserFingerprintsResponse:
    links = await fingerprint_service.get_user_fingerprints(user_id, limit=limit)
    return UserFingerprintsResponse(
        user_id=user_id,
        fingerprints=[FingerprintUserLink(**link) for link in links],
    )


@router.get(
    "/visitor/{visitor_id}",
    response_model=VisitorFingerprintDetailResponse,
    summary="List all accounts seen on a device fingerprint",
    dependencies=[Depends(verify_super_admin_key)],
)
async def get_visitor_fingerprints(
    visitor_id: str,
    fingerprint_service: FingerprintService = Depends(get_fingerprint_service),
) -> VisitorFingerprintDetailResponse:
    detail = await fingerprint_service.get_visitor_detail(visitor_id)
    if not detail:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Fingerprint not found",
        )
    return VisitorFingerprintDetailResponse(
        visitor_id=detail["visitor_id"],
        account_count=detail["account_count"],
        is_blocked=detail["is_blocked"],
        accounts=[FingerprintUserLink(**account) for account in detail["accounts"]],
    )


@router.post(
    "/block",
    response_model=FingerprintBlockResponse,
    summary="Block a device fingerprint (all linked accounts)",
    dependencies=[Depends(verify_super_admin_key)],
)
async def block_fingerprint(
    body: FingerprintBlockRequest,
    fingerprint_service: FingerprintService = Depends(get_fingerprint_service),
) -> FingerprintBlockResponse:
    modified = await fingerprint_service.block_visitor(
        body.visitor_id,
        reason=body.reason,
        blocked_by=body.blocked_by,
    )
    if modified == 0:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="No records found for this visitor_id",
        )
    return FingerprintBlockResponse(
        visitor_id=body.visitor_id,
        modified_count=modified,
    )


@router.post(
    "/unblock",
    response_model=FingerprintBlockResponse,
    summary="Remove block from a device fingerprint",
    dependencies=[Depends(verify_super_admin_key)],
)
async def unblock_fingerprint(
    body: FingerprintBlockRequest,
    fingerprint_service: FingerprintService = Depends(get_fingerprint_service),
) -> FingerprintBlockResponse:
    modified = await fingerprint_service.unblock_visitor(body.visitor_id)
    if modified == 0:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="No records found for this visitor_id",
        )
    return FingerprintBlockResponse(
        visitor_id=body.visitor_id,
        modified_count=modified,
    )
