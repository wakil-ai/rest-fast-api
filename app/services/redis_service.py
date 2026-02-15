import json
import threading
from queue import Empty, Queue

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

        # Background log-flushing thread
        self._log_queue: Queue = Queue()
        self._log_thread = threading.Thread(
            target=self._flush_logs_forever, daemon=True
        )
        self._log_thread.start()

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
        """Loguru sink — enqueues log entries for async flushing to Redis.

        Non-blocking: the actual Redis writes happen in a background thread.
        """
        try:
            record = message.record
            level = record["level"].name.lower()

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
            self._log_queue.put_nowait((f"logs:{level}", log_data))
        except Exception:
            pass  # never let logging break the app

    def _flush_logs_forever(self):
        """Background worker — drains the queue and writes to Redis in batches."""
        while True:
            try:
                key, data = self._log_queue.get(timeout=1.0)
                # Drain remaining items into a batch
                batch: list[tuple[str, str]] = [(key, data)]
                while len(batch) < 200:
                    try:
                        batch.append(self._log_queue.get_nowait())
                    except Empty:
                        break

                pipe = self.redis.pipeline(transaction=False)
                for k, d in batch:
                    pipe.rpush(k, d)
                    pipe.expire(k, settings.REDIS_EXPIRATION_SECONDS)
                pipe.execute()
            except Empty:
                continue
            except Exception:
                pass  # Redis down — silently drop, don’t crash the worker

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
