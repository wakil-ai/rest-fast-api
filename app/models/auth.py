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
    first_name: str | None = Field(None, description="First name of the user")
    last_name: str | None = Field(None, description="Last name of the user")
    username: str | None = Field(None, description="Username of the user")
    phone_number: str | None = Field(None, description="Phone number of the user")
    
class DTUserCreateResponse(BaseModel):
    success: bool = Field(..., description="Indicates if the user was created successfully")
    user_id: str = Field(..., description="The ID of the created user (internal user ID)")