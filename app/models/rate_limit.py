from pydantic import BaseModel


class RateLimitResponse(BaseModel):
    user_id: str
    remaining_credits: int
    daily_credit_limit: int
    credit_costs: dict


class ResetLimitRequest(BaseModel):
    user_id: str
