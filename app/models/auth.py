from pydantic import BaseModel, Field


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
