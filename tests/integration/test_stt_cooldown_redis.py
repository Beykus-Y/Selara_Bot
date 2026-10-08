"""Run against the real Redis service in backend CI, never a process-local fake."""

import asyncio
import os
from uuid import uuid4

import pytest
from redis.asyncio import Redis

from selara.infrastructure.stt.cooldown import claim_stt_cooldown

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]


@pytest.fixture
def redis_url():
    url = os.getenv("TEST_REDIS_URL")
    if not url:
        pytest.skip("TEST_REDIS_URL is not set")
    return url


async def test_concurrent_replicas_and_restart_share_one_expiring_cooldown(redis_url):
    chat_id = -int(uuid4().int % (2**62))
    kwargs = dict(redis_url=redis_url, chat_id=chat_id, user_id=1, cooldown_seconds=60)
    key = f"selara:stt:cooldown:{chat_id}:1"
    async with Redis.from_url(redis_url) as redis:
        try:
            # Every claim opens/closes a fresh client, as separate replicas and
            # a restarted process do; there is no shared Python state.
            results = await asyncio.gather(*(claim_stt_cooldown(**kwargs) for _ in range(10)))
            assert sum(results) == 1
            assert not await claim_stt_cooldown(**kwargs)
            assert 0 < await redis.pttl(key) <= 60000
            assert await claim_stt_cooldown(**{**kwargs, "user_id": 2})
            assert await claim_stt_cooldown(**{**kwargs, "chat_id": chat_id - 1})
            # Shorten this TTL to observe real automatic expiry without a long wait.
            await redis.pexpire(key, 20)
            for _ in range(100):
                if not await redis.exists(key):
                    break
                await asyncio.sleep(0.02)
            assert not await redis.exists(key)
            assert await claim_stt_cooldown(**kwargs)
        finally:
            await redis.delete(key, f"selara:stt:cooldown:{chat_id}:2", f"selara:stt:cooldown:{chat_id - 1}:1")
