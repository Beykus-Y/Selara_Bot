"""Redis 5→8 guard: pooled async client recovers after idle TCP peer closure.

Real-Redis integration test; no Redis server restart or durable data deletion.
Models a dropped, idle pool connection, not every possible network partition.
"""
from __future__ import annotations

import asyncio
import os
from uuid import uuid4

import pytest
from redis.asyncio import Redis
from redis.exceptions import ConnectionError as RedisConnectionError
from redis.exceptions import TimeoutError as RedisTimeoutError

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]


async def test_pooled_client_recovers_idle_killed_connection_and_preserves_state() -> None:
    url = os.getenv("TEST_REDIS_URL")
    if not url:
        pytest.skip("TEST_REDIS_URL is not set")

    key = f"selara:redis:reconnect:ci:{uuid4().hex}"
    # health_check_interval makes this exercise the periodic-check path also
    # used in any future connection-pool configuration using proactive checks.
    async with Redis.from_url(
        url, decode_responses=True, health_check_interval=1,
        socket_connect_timeout=2, socket_timeout=2,
    ) as client, Redis.from_url(url, decode_responses=True) as killer:
        try:
            assert await client.set(key, "persistent-value", ex=60)
            client_id = await client.client_id()
            assert isinstance(client_id, int) and client_id > 0
            # Kill only the test client's known connection. The Redis service,
            # writer lease and other CI connections remain untouched.
            killed = await killer.execute_command("CLIENT", "KILL", "ID", client_id)
            assert int(killed) == 1
            await asyncio.sleep(1.1)

            # One transient socket read may fail in versions without an implicit
            # retry; later commands must establish a fresh pooled connection.
            recovered = False
            for _ in range(3):
                try:
                    if await client.ping() and await client.get(key) == "persistent-value":
                        recovered = True
                        break
                except (RedisConnectionError, RedisTimeoutError):
                    await asyncio.sleep(0.05)
            assert recovered, "Redis async pooled connection never recovered after idle close"

            # A subsequent write/read is required to check the pool did not
            # continue using the stale socket after the first successful ping.
            assert await client.set(key, "reconnected", ex=60)
            assert await client.get(key) == "reconnected"
        finally:
            await killer.delete(key)
