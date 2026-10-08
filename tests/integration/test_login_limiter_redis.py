import asyncio
import os
from uuid import uuid4

import pytest
from redis.asyncio import Redis

from selara.web.login_limiter import RedisLoginAttemptLimiter

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]


async def test_replicas_and_restart_share_atomic_sliding_window():
    url = os.getenv("TEST_REDIS_URL")
    if not url:
        pytest.skip("TEST_REDIS_URL is not set")
    host = uuid4().hex
    web_key, admin_key = f"web:{host}", f"admin:{host}"
    clients = [RedisLoginAttemptLimiter(redis_url=url, limit=3, window_seconds=60) for _ in range(12)]
    async with Redis.from_url(url) as redis:
        try:
            tokens = await asyncio.gather(*(limiter.reserve(web_key) for limiter in clients))
            accepted = [token for token in tokens if token is not None]
            assert len(accepted) == 3
            fresh_process = RedisLoginAttemptLimiter(redis_url=url, limit=3, window_seconds=60)
            assert await fresh_process.reserve(web_key) is None
            assert await fresh_process.reserve(admin_key) is not None
            assert 0 < await redis.pttl(fresh_process.store_key(web_key)) <= 60000
            await fresh_process.release(web_key, "not-owned")
            assert await fresh_process.reserve(web_key) is None
            await fresh_process.release(web_key, accepted[0])
            assert await fresh_process.reserve(web_key) is not None
            assert await fresh_process.reserve(web_key) is None
        finally:
            await redis.delete(clients[0].store_key(web_key), clients[0].store_key(admin_key))


async def test_expiry_removes_stale_counter_without_process_cleanup():
    url = os.getenv("TEST_REDIS_URL")
    if not url:
        pytest.skip("TEST_REDIS_URL is not set")
    key = f"web:{uuid4().hex}"
    limiter = RedisLoginAttemptLimiter(redis_url=url, limit=1, window_seconds=0.5)
    async with Redis.from_url(url) as redis:
        try:
            assert await limiter.reserve(key) is not None
            assert await RedisLoginAttemptLimiter(redis_url=url, limit=1, window_seconds=0.5).reserve(key) is None
            await asyncio.sleep(0.6)
            assert await redis.exists(limiter.store_key(key)) == 0
            assert await limiter.reserve(key) is not None
        finally:
            await redis.delete(limiter.store_key(key))
