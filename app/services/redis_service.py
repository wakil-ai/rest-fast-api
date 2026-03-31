import json
from datetime import datetime
from decimal import Decimal

import redis

from app.core.config import settings
from app.core.logger import logger


class DateTimeEncoder(json.JSONEncoder):
    """Custom JSON encoder for datetime and Decimal objects."""

    def default(self, obj):
        if isinstance(obj, datetime):
            return obj.isoformat()
        elif isinstance(obj, Decimal):
            return float(obj)
        return super().default(obj)


class RedisService:
    def __init__(self):
        self.redis = redis.Redis(
            host=settings.REDIS_HOST,
            port=settings.REDIS_PORT,
            db=0,
            decode_responses=True,
        )

        logger.info(
            "[RedisService] Initialized RedisService with connection to "
            f"{settings.REDIS_HOST}:{settings.REDIS_PORT}"
        )

    def cache_get(self, key: str) -> str | None:
        """Get a value from cache. Returns None if key doesn't exist or on error."""
        try:
            return self.redis.get(key)
        except Exception:
            return None

    def cache_set(self, key: str, value, ttl_seconds: int = 86400) -> bool:
        """Set a value in cache with TTL. Handles datetime serialization. Returns True on success, False on error."""
        try:
            # If value is a dict, serialize with custom encoder for datetime support
            if isinstance(value, dict):
                json_value = json.dumps(value, cls=DateTimeEncoder)
            else:
                json_value = (
                    value
                    if isinstance(value, str)
                    else json.dumps(value, cls=DateTimeEncoder)
                )

            self.redis.set(key, json_value, ex=ttl_seconds)
            return True
        except Exception as e:
            logger.error(f"[RedisService] Error setting Redis cache key {key}: {e}")
            return False

    def invalidate_cache(self, key: str) -> bool:
        """Delete a cache key. Returns True on success, False on error."""
        try:
            self.redis.delete(key)
            return True
        except Exception:
            return False
