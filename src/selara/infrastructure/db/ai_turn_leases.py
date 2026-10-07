"""Durable per-key leases that serialise AI turns across bot instances.

A lease is taken and renewed in short transactions, so no database transaction or connection is held while the
provider call runs. While a turn runs, a heartbeat keeps extending the lease, so provider retries cannot let a second
instance start on the same key. Expiry uses the database clock, so replicas with skewed clocks agree on it. If an
instance dies mid-turn, its heartbeat stops and the lease expires after ``AI_TURN_LEASE_TTL_SECONDS``.
"""

from __future__ import annotations

import asyncio
import logging
import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import timedelta

from sqlalchemy import delete, func, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from selara.infrastructure.db.models import AiTurnLeaseModel

log = logging.getLogger(__name__)

AI_TURN_LEASE_TTL_SECONDS = 60.0
_HEARTBEATS_PER_TTL = 3


async def try_acquire_ai_turn_lease(
    *,
    session_factory: async_sessionmaker[AsyncSession],
    lease_key: str,
    ttl_seconds: float,
) -> str | None:
    """Return the owner token when the lease was taken, or None while another live lease holds the key."""
    owner_token = uuid.uuid4().hex
    expires_at = func.now() + timedelta(seconds=ttl_seconds)
    statement = (
        pg_insert(AiTurnLeaseModel)
        .values(lease_key=lease_key, owner_token=owner_token, lease_expires_at=expires_at)
        .on_conflict_do_update(
            index_elements=[AiTurnLeaseModel.lease_key],
            set_={"owner_token": owner_token, "lease_expires_at": expires_at},
            where=AiTurnLeaseModel.lease_expires_at < func.now(),
        )
        .returning(AiTurnLeaseModel.owner_token)
    )
    async with session_factory() as session:
        row = (await session.execute(statement)).first()
        await session.commit()
    return owner_token if row is not None else None


async def renew_ai_turn_lease(
    *,
    session_factory: async_sessionmaker[AsyncSession],
    lease_key: str,
    owner_token: str,
    ttl_seconds: float,
) -> bool:
    """Extend the lease if this owner still holds it. False means the lease was lost."""
    async with session_factory() as session:
        result = await session.execute(
            update(AiTurnLeaseModel)
            .where(
                AiTurnLeaseModel.lease_key == lease_key,
                AiTurnLeaseModel.owner_token == owner_token,
            )
            .values(lease_expires_at=func.now() + timedelta(seconds=ttl_seconds))
            .execution_options(synchronize_session=False)
        )
        await session.commit()
    return result.rowcount == 1


async def release_ai_turn_lease(
    *,
    session_factory: async_sessionmaker[AsyncSession],
    lease_key: str,
    owner_token: str,
) -> None:
    """Drop the lease only if this owner still holds it; an expired lease taken over by someone else is left alone."""
    async with session_factory() as session:
        await session.execute(
            delete(AiTurnLeaseModel).where(
                AiTurnLeaseModel.lease_key == lease_key,
                AiTurnLeaseModel.owner_token == owner_token,
            )
        )
        await session.commit()


async def _keep_renewed(
    *,
    session_factory: async_sessionmaker[AsyncSession],
    lease_key: str,
    owner_token: str,
    ttl_seconds: float,
) -> None:
    while True:
        await asyncio.sleep(ttl_seconds / _HEARTBEATS_PER_TTL)
        try:
            if not await renew_ai_turn_lease(
                session_factory=session_factory, lease_key=lease_key, owner_token=owner_token, ttl_seconds=ttl_seconds
            ):
                log.warning("AI turn lease was lost while the turn was running key=%s", lease_key)
                return
        except Exception:
            # A failed heartbeat is retried on the next tick. Only a run of failures lets the lease expire.
            log.exception("Failed to renew AI turn lease key=%s", lease_key)


@asynccontextmanager
async def ai_turn_lease(
    *,
    session_factory: async_sessionmaker[AsyncSession],
    lease_key: str,
    ttl_seconds: float = AI_TURN_LEASE_TTL_SECONDS,
) -> AsyncIterator[bool]:
    """Yield True while this caller holds the lease for ``lease_key``, False when another turn is running.

    The lease is renewed in the background for as long as the block runs, so a slow turn with provider retries
    keeps the key. It is released on exit, and expires on its own only when the instance dies.
    """
    owner_token = await try_acquire_ai_turn_lease(
        session_factory=session_factory, lease_key=lease_key, ttl_seconds=ttl_seconds
    )
    if owner_token is None:
        yield False
        return
    heartbeat = asyncio.create_task(
        _keep_renewed(
            session_factory=session_factory, lease_key=lease_key, owner_token=owner_token, ttl_seconds=ttl_seconds
        )
    )
    try:
        yield True
    finally:
        heartbeat.cancel()
        try:
            await release_ai_turn_lease(session_factory=session_factory, lease_key=lease_key, owner_token=owner_token)
        except Exception:
            # The lease still expires on its own, so a failed release only delays the next turn for this key.
            log.exception("Failed to release AI turn lease key=%s", lease_key)
