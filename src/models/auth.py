from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field


class TelegramAuth(BaseModel):
    id: int | None = None
    first_name: str | None = None
    last_name: str | None = None
    username: str | None = None
    photo_url: str | None = None
    auth_date: str | None = None
    hash: str | None = None


class TelegramDataError(Exception):
    pass


class TelegramDataIsOutdated(Exception):
    pass


class DTUserCreateRequest(BaseModel):
    user_id: str = Field(..., description="User ID")
    phone_number: str = Field(
        ..., description="Phone number of the user"
    )  # required for DT integration
    first_name: str = Field(..., description="First name of the user")
    last_name: str | None = Field(None, description="Last name of the user")
    username: str | None = Field(None, description="Username of the user")


class DTUserCreateResponse(BaseModel):
    success: bool = Field(
        ..., description="Indicates if the user was created successfully"
    )
    user_id: str = Field(
        ..., description="The ID of the created user (internal user ID)"
    )


class JWTPayload(BaseModel):
    sub: str = Field(..., min_length=1)
    exp: datetime
    iat: datetime
    jti: str = Field(..., min_length=1)
    typ: Literal["access_token"]
    iss: str = Field(..., min_length=1)
    aud: str = Field(..., min_length=1)
