import sys
from functools import lru_cache

from loguru import logger as loguru_logger

from core.config import settings


@lru_cache
def get_logger():
    loguru_logger.remove()  # Remove default logger

    log_format = (
        "<green>{time:HH:mm:ss}</green> | "
        "<level>{level: <8}</level> | "
        "<cyan>{name}</cyan>:<cyan>{function}</cyan>:<cyan>{line}</cyan> - "
        "{message}"
    )

    # Add console sink with color
    loguru_logger.add(
        sys.stdout,
        format=log_format,
        level="DEBUG" if settings.DEBUG else "INFO",
        colorize=True,
    )

    return loguru_logger


logger = get_logger()
