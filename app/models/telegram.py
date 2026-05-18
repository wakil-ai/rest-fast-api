from pydantic import BaseModel, Field


class TelegramChatUpsert(BaseModel):
    chat_id: int = Field(..., description="Telegram chat_id")
    user_id: int = Field(..., description="Telegram user id")
    username: str | None = Field(default=None, description="Telegram username")
    first_name: str | None = Field(default=None, description="Telegram first name")
    last_name: str | None = Field(default=None, description="Telegram last name")
    language_code: str | None = Field(default=None, description="Telegram language")


class TelegramChatsSaveRequest(BaseModel):
    chat: TelegramChatUpsert | None = Field(
        default=None, description="Single chat record to upsert"
    )
    chats: list[TelegramChatUpsert] | None = Field(
        default=None, description="Multiple chat records to upsert"
    )


class TelegramChatEntry(BaseModel):
    chat_id: int
    user_id: int
    is_active: bool = True
    created_at_ms: int | None = None
    updated_at_ms: int | None = None


class TelegramChatsListResponse(BaseModel):
    chats: list[TelegramChatEntry]
    count: int
