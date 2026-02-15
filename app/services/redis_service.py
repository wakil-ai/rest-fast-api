import json
from datetime import datetime

import redis

from app.core.config import settings


class RedisService:
    _instance = None

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
        return cls._instance

    def __init__(self):
        if hasattr(self, "_initialized"):
            return
        self._initialized = True
        self.redis = redis.Redis(
            host=settings.REDIS_HOST,
            port=settings.REDIS_PORT,
            db=0,
            decode_responses=True,
        )

    def redis_sink(
        self,
        key: str,
        value: str,
        expire_seconds: int = settings.REDIS_EXPIRATION_SECONDS,
    ):
        """Store a value in Redis with an optional expiration time."""
        try:
            self.redis.set(key, value, ex=expire_seconds)
        except Exception as e:
            print(f"Error setting Redis key {key}: {e}")

    def loguru_sink(self, message):
        """Loguru sink — pushes each log entry into a Redis list keyed by level.

        Redis keys: ``logs:info``, ``logs:warning``, ``logs:error``, etc.
        Each entry is a JSON string with timestamp, level, location, and message.
        """
        try:
            record = message.record
            level = record["level"].name.lower()  # info / warning / error / …

            log_data = json.dumps(
                {
                    "timestamp": record["time"].strftime("%Y-%m-%d %H:%M:%S"),
                    "level": level,
                    "module": record["name"],
                    "function": record["function"],
                    "line": record["line"],
                    "message": record["message"],
                },
                ensure_ascii=False,
            )

            redis_key = f"logs:{level}"
            self.redis.rpush(redis_key, log_data)
            self.redis.expire(redis_key, settings.REDIS_EXPIRATION_SECONDS)
        except Exception as e:
            print(f"Error pushing log to Redis: {e}")

    def get_logs(self, level: str, limit: int = 100) -> list[dict]:
        """Retrieve the latest *limit* log entries for a given level."""
        try:
            redis_key = f"logs:{level}"
            raw = self.redis.lrange(redis_key, -limit, -1)
            return [json.loads(entry) for entry in raw]
        except Exception as e:
            print(f"Error reading logs from Redis: {e}")
            return []

    def clear_logs(self, level: str) -> int:
        """Delete all log entries for a given level. Returns number of entries removed."""
        try:
            redis_key = f"logs:{level}"
            count = self.redis.llen(redis_key)
            self.redis.delete(redis_key)
            return count
        except Exception as e:
            print(f"Error clearing logs from Redis: {e}")
            return 0