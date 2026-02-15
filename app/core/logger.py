import sys

from loguru import logger as loguru_logger
from app.core.config import settings
from app.services.redis_service import RedisService

redis_service = RedisService()


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
            redis_service.loguru_sink,
            level="DEBUG" if settings.DEBUG else "INFO",
            serialize=False,
        )
    except Exception as e:
        loguru_logger.warning(f"Redis sink not available, logs won't be persisted: {e}")

    return loguru_logger


logger = get_logger()
