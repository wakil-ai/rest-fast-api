from typing import Optional
from pydantic import BaseModel

class TelegramAuth(BaseModel):
    id: Optional[int] = None
    first_name: Optional[str] = None
    last_name: Optional[str] = None
    username: Optional[str] = None
    photo_url: Optional[str] = None
    auth_date: Optional[str] = None
    hash: Optional[str] = None

class TelegramDataError(Exception):
    pass


class TelegramDataIsOutdated(Exception):
    pass