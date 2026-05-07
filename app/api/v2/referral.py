from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, status

from app.core.dependencies import get_referral_service
from app.models.referral import (
    ReferralSourceStats,
    ReferralStatsResponse,
    ReferralTrackResponse,
)
from app.security import verify_api_key
from app.services.referral_service import ReferralService

router = APIRouter(prefix="/referrals", tags=["Referrals"])


@router.post(
    "/track",
    response_model=ReferralTrackResponse,
    summary="Track a referral website source",
)
async def track_referral(
    source: Annotated[str, Query(alias="from", min_length=1, max_length=300)],
    referral_service: ReferralService = Depends(get_referral_service),
):
    try:
        referral = await referral_service.track_source(source)
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=str(exc),
        ) from exc

    return ReferralTrackResponse(
        source=referral["source"],
        total_count=referral["total_count"],
    )


@router.get(
    "/stats",
    response_model=ReferralStatsResponse,
    summary="List referral source counts",
    dependencies=[Depends(verify_api_key)],
)
async def get_referral_stats(
    limit: Annotated[int, Query(ge=1, le=500)] = 100,
    referral_service: ReferralService = Depends(get_referral_service),
):
    stats = await referral_service.get_stats(limit=limit)
    return ReferralStatsResponse(
        sources=[
            ReferralSourceStats(
                source=item["source"],
                total_count=item["total_count"],
                first_seen_at=item["first_seen_at"],
                last_seen_at=item["last_seen_at"],
            )
            for item in stats
        ]
    )
