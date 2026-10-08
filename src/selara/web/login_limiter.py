import hashlib
import logging
import math
from uuid import uuid4

from redis.asyncio import Redis
from redis.exceptions import RedisError

logger = logging.getLogger(__name__)

_RESERVE = """
local clock = redis.call('TIME')
local now = tonumber(clock[1]) * 1000 + math.floor(tonumber(clock[2]) / 1000)
redis.call('ZREMRANGEBYSCORE', KEYS[1], '-inf', now - tonumber(ARGV[2]))
if redis.call('ZCARD', KEYS[1]) >= tonumber(ARGV[1]) then return 0 end
redis.call('ZADD', KEYS[1], now, ARGV[3])
redis.call('PEXPIRE', KEYS[1], ARGV[2])
return 1
"""
_RELEASE = """
redis.call('ZREM', KEYS[1], ARGV[1])
if redis.call('ZCARD', KEYS[1]) == 0 then redis.call('DEL', KEYS[1]) end
return 1
"""


class LoginLimiterUnavailable(Exception):
    pass


class RedisLoginAttemptLimiter:
    """Failed/in-flight attempts share a durable sliding window across replicas."""

    def __init__(self, *, redis_url: str, limit: int, window_seconds: float):
        self.redis_url = redis_url
        self.limit = max(1, int(limit))
        self.window_ms = max(1, math.ceil(window_seconds * 1000))

    @staticmethod
    def store_key(key: str) -> str:
        return "selara:login-attempts:" + hashlib.sha256(key.encode()).hexdigest()

    def _client(self):
        return Redis.from_url(self.redis_url, socket_connect_timeout=2, socket_timeout=2)

    async def reserve(self, key: str) -> str | None:
        token = uuid4().hex
        try:
            async with self._client() as client:
                accepted = await client.eval(_RESERVE, 1, self.store_key(key), self.limit, self.window_ms, token)
        except (RedisError, OSError):
            logger.warning("Shared login limiter unavailable; admission refused")
            raise LoginLimiterUnavailable("Сервис входа временно недоступен. Попробуйте позже.") from None
        return token if accepted else None

    async def release(self, key: str, token: str) -> None:
        # Refund only this successful attempt; never erase concurrent failures.
        try:
            async with self._client() as client:
                await client.eval(_RELEASE, 1, self.store_key(key), token)
        except (RedisError, OSError):
            # Authentication already succeeded. Its conservative slot expires by TTL.
            logger.warning("Shared login limiter success cleanup unavailable; slot will expire")
