"""Atomic, expiring admission for paid instant STT, shared across bot replicas."""

from __future__ import annotations

import logging
import math

from redis.asyncio import Redis
from redis.exceptions import RedisError

log = logging.getLogger(__name__)


async def claim_stt_cooldown(
    *, redis_url: str, chat_id: int, user_id: int, cooldown_seconds: float,
) -> bool:
    # Zero explicitly disables throttling, matching STT_COOLDOWN_SECONDS semantics.
    if cooldown_seconds <= 0:
        return True
    try:
        # Each short admission uses its own bounded connection lifetime. No
        # process-global cache is kept, and Redis expires the reservation itself.
        async with Redis.from_url(redis_url, socket_connect_timeout=2, socket_timeout=2) as redis:
            return bool(await redis.set(
                f"selara:stt:cooldown:{chat_id}:{user_id}", "1", nx=True,
                px=max(1, math.ceil(cooldown_seconds * 1000)),
            ))
    except (RedisError, OSError):
        # Fail closed: an outage must not silently permit unlimited paid calls.
        # Do not log the URL/exception text, which may contain Redis credentials.
        log.warning("Instant STT admission unavailable; refusing request chat_id=%s user_id=%s", chat_id, user_id)
        return False
