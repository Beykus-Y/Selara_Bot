"""Durable per-key leases that serialise AI turns across bot instances.

A lease is taken in a short transaction and released in another one, so no database transaction or connection is
held while the provider call runs. Expiry uses the database clock, so replicas with skewed clocks agree on it. If an
instance dies mid-turn, its lease expires on its own and the key becomes available again.
"""

from __future__ import annotations

import logging
import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import timedelta

from sqlalchemy import delete, func
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from selara.infrastructure.db.models import AiTurnLeaseModel

log = logging.getLogger(__name__)


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


@asynccontextmanager
async def ai_turn_lease(
    *,
    session_factory: async_sessionmaker[AsyncSession],
    lease_key: str,
    ttl_seconds: float,
) -> AsyncIterator[bool]:
    """Yield True while this caller holds the lease for ``lease_key``, False when another turn is running."""
    owner_token = await try_acquire_ai_turn_lease(
        session_factory=session_factory, lease_key=lease_key, ttl_seconds=ttl_seconds
    )
    if owner_token is None:
        yield False
        return
    try:
        yield True
    finally:
        try:
            await release_ai_turn_lease(session_factory=session_factory, lease_key=lease_key, owner_token=owner_token)
        except Exception:
            # The lease still expires on its own, so a failed release only delays the next turn for this key.
            log.exception("Failed to release AI turn lease key=%s", lease_key)
