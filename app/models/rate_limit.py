from pydantic import BaseModel


class RateLimitResponse(BaseModel):
    user_id: str
    remaining_credits: int
    daily_credit_limit: int
    effective_daily_credit_limit: int
    today_credits_used: int
    uses_combined_credit_pool: bool = True


class ResetLimitRequest(BaseModel):
    user_id: str
