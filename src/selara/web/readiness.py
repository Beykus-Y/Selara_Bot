"""Bounded readiness probes; optional AI/gacha providers are not required."""

import asyncio
from datetime import datetime, timezone

from redis.asyncio import Redis
from sqlalchemy import select


def polling_ready(state: dict, *, now: datetime | None = None) -> bool:
    heartbeat = state.get("heartbeat_at")
    if state.get("running") is not True or not isinstance(heartbeat, datetime):
        return False
    if heartbeat.tzinfo is None:
        heartbeat = heartbeat.replace(tzinfo=timezone.utc)
    age = ((now or datetime.now(timezone.utc)) - heartbeat).total_seconds()
    return 0 <= age <= 45


async def database_ready(session_factory) -> bool:
    try:
        async with asyncio.timeout(2):
            async with session_factory() as session:
                await session.execute(select(1))
        return True
    except Exception:
        return False


async def redis_ready(redis_url: str) -> bool:
    try:
        async with asyncio.timeout(2):
            client = Redis.from_url(redis_url, socket_connect_timeout=1, socket_timeout=1)
            try:
                return bool(await client.ping())
            finally:
                await client.aclose()
    except Exception:
        return False
