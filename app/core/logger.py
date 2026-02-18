import sys
from functools import lru_cache

from loguru import logger as loguru_logger

from app.core.config import settings


# Lazy logger initialization with Redis sink
def _redis_sink(message):
    """Lazy Redis sink — resolves RedisService on first log write, not at import time."""
    from app.core.dependencies import get_redis_service

    get_redis_service().loguru_sink(message)


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

    # Add Redis sink — every log is persisted under logs:<level>
    try:
        loguru_logger.add(
            _redis_sink,
            level="DEBUG" if settings.DEBUG else "INFO",
            serialize=False,
        )
    except Exception as e:
        loguru_logger.warning(f"Redis sink not available, logs won't be persisted: {e}")

    return loguru_logger


logger = get_logger()
