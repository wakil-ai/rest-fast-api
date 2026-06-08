from datetime import datetime

from pydantic import BaseModel, Field


class ReferralTrackResponse(BaseModel):
    success: bool = True
    source: str = Field(..., description="Normalized referring website hostname")
    total_count: int = Field(..., description="Total tracked visits for this source")


class ReferralSourceStats(BaseModel):
    source: str
    total_count: int
    first_seen_at: datetime
    last_seen_at: datetime


class ReferralStatsResponse(BaseModel):
    sources: list[ReferralSourceStats]
