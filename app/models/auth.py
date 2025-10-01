from typing import Optional
from pydantic import BaseModel
from dataclasses import dataclass
from app.core.config import settings
from enum import Enum

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

class Size(Enum):
    """Button Size variants"""
    LARGE: str = 'large'
    MEDIUM: str = 'medium'
    SMALL: str = 'small'

@dataclass
class BotConfig:
    telegram_token: str
    telegram_login: str


@dataclass
class AppConfig:
    bot: BotConfig
    
def load_config() -> AppConfig:
    return AppConfig(
        bot=BotConfig(
            telegram_token=settings.TELEGRAM_BOT_TOKEN,
            telegram_login=settings.TELEGRAM_BOT_LOGIN,
        ),
    )