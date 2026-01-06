from pydantic import BaseModel


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
