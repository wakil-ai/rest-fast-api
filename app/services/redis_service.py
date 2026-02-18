import json
import asyncio
from typing import Any

import redis

from app.core.config import settings


class RedisService:
    def __init__(self):
        self.redis = redis.Redis(
            host=settings.REDIS_HOST,
            port=settings.REDIS_PORT,
            db=0,
            decode_responses=True,
        )

        self._log_queue: asyncio.Queue[tuple[str, str]] | None = None
        self._worker_task: asyncio.Task | None = None

    def _ensure_worker_started(self) -> bool:
        if self._worker_task and not self._worker_task.done():
            return True

        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            # No event loop (e.g., import-time logging). Drop async persistence.
            return False

        self._log_queue = asyncio.Queue()
        self._worker_task = loop.create_task(self._flush_logs_forever())
        return True

    async def redis_sink(
        self,
        key: str,
        value: str,
        expire_seconds: int = settings.REDIS_EXPIRATION_SECONDS,
    ):
        """Store a value in Redis with an optional expiration time."""
        try:
            await asyncio.to_thread(self.redis.set, key, value, ex=expire_seconds)
        except Exception as e:
            print(f"Error setting Redis key {key}: {e}")

    def loguru_sink(self, message):
        """Loguru sink — enqueues log entries for async flushing to Redis.

        Non-blocking: the actual Redis writes happen in a background thread.
        """
        try:
            if not self._ensure_worker_started() or self._log_queue is None:
                return
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

    async def _flush_logs_forever(self) -> None:
        """Background worker — drains the queue and writes to Redis in batches."""
        while True:
            try:
                if self._log_queue is None:
                    await asyncio.sleep(0.2)
                    continue
                key, data = await self._log_queue.get()

                batch: list[tuple[str, str]] = [(key, data)]
                while len(batch) < 200:
                    try:
                        batch.append(self._log_queue.get_nowait())
                    except asyncio.QueueEmpty:
                        break

                await asyncio.to_thread(self._write_log_batch, batch)
            except Exception:
                # Redis down — silently drop, don’t crash the worker
                await asyncio.sleep(0.2)

    def _write_log_batch(self, batch: list[tuple[str, str]]) -> None:
        pipe = self.redis.pipeline(transaction=False)
        for k, d in batch:
            pipe.rpush(k, d)
            pipe.expire(k, settings.REDIS_EXPIRATION_SECONDS)
        pipe.execute()

    async def get_logs(self, level: str, limit: int = 100) -> list[dict]:
        """Retrieve the latest *limit* log entries for a given level."""
        try:
            redis_key = f"logs:{level}"
            raw = await asyncio.to_thread(self.redis.lrange, redis_key, -limit, -1)
            return [json.loads(entry) for entry in raw]
        except Exception as e:
            print(f"Error reading logs from Redis: {e}")
            return []

    async def clear_logs(self, level: str) -> int:
        """Delete all log entries for a given level. Returns number of entries removed."""
        try:
            redis_key = f"logs:{level}"
            count = await asyncio.to_thread(self.redis.llen, redis_key)
            await asyncio.to_thread(self.redis.delete, redis_key)
            return count
        except Exception as e:
            print(f"Error clearing logs from Redis: {e}")
            return 0
