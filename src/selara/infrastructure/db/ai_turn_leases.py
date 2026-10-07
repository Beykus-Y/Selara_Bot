"""Durable per-key leases that serialise AI turns across bot instances.

A lease is taken and renewed in short transactions, so no database transaction or connection is held while the
provider call runs. While a turn runs, a heartbeat keeps extending the lease, so provider retries cannot let a second
instance start on the same key. Expiry uses the database clock, so replicas with skewed clocks agree on it. If an
instance dies mid-turn, its heartbeat stops and the lease expires after ``AI_TURN_LEASE_TTL_SECONDS``.

A turn that loses its key is fenced at its checkpoints. ``AiTurnLease.confirm`` re-checks ownership in the database and
extends the lease for a full TTL, so a step that follows a passing check runs while the key is still held. A turn
calls it before each model round, each tool call, and before it publishes or commits. A running step is never
cancelled, because a half-finished tool call would leave a side effect without its audit row.
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


class AiTurnLeaseLostError(RuntimeError):
    """The turn no longer owns its lease, so it must not run another step or save or publish a result."""


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
    """Extend the lease if this owner still holds it. False means the lease was lost.

    An expired lease is never revived, even by the owner whose token it still carries: a heartbeat that stalled past
    the TTL must not take the key back from a replica that may already have taken it over.
    """
    async with session_factory() as session:
        result = await session.execute(
            update(AiTurnLeaseModel)
            .where(
                AiTurnLeaseModel.lease_key == lease_key,
                AiTurnLeaseModel.owner_token == owner_token,
                AiTurnLeaseModel.lease_expires_at > func.now(),
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


class AiTurnLease:
    """A lease this turn holds. Call ``confirm`` before each step that acts on the outside world or saves a result."""

    def __init__(
        self,
        *,
        session_factory: async_sessionmaker[AsyncSession],
        lease_key: str,
        owner_token: str,
        ttl_seconds: float,
        acquired_at: float,
    ) -> None:
        self._session_factory = session_factory
        self.lease_key = lease_key
        self._owner_token = owner_token
        self._ttl_seconds = ttl_seconds
        # Loop time from before the last successful renewal began. The database sets the expiry no earlier than that,
        # so the deadline derived from it always falls before the lease expires.
        self._renewed_at = acquired_at
        self._lost = False

    @property
    def lost(self) -> bool:
        return self._lost

    async def confirm(self) -> None:
        """Re-check ownership in the database. A passing check extends the lease for a full TTL.

        Raises AiTurnLeaseLostError when the key is no longer held. Database errors propagate, so the caller fails the
        step it was about to take.
        """
        await self._renew()

    def _mark_lost(self, reason: str) -> None:
        if not self._lost:
            self._lost = True
            log.warning("AI turn lease lost key=%s: %s", self.lease_key, reason)

    async def _renew(self) -> None:
        """Renew before the local deadline. Raises AiTurnLeaseLostError when the lease is gone; other errors propagate."""
        if self._lost:
            raise AiTurnLeaseLostError(self.lease_key)
        started = asyncio.get_running_loop().time()
        try:
            async with asyncio.timeout_at(self._renewed_at + self._ttl_seconds) as deadline:
                renewed = await renew_ai_turn_lease(
                    session_factory=self._session_factory,
                    lease_key=self.lease_key,
                    owner_token=self._owner_token,
                    ttl_seconds=self._ttl_seconds,
                )
        except TimeoutError:
            # A renewal that hangs past the TTL cannot be trusted: the database may already have let the lease expire.
            if not deadline.expired():
                raise
            self._mark_lost("the renewal did not finish within the TTL")
            raise AiTurnLeaseLostError(self.lease_key) from None
        if not renewed:
            self._mark_lost("the lease expired or another owner holds the key")
            raise AiTurnLeaseLostError(self.lease_key)
        self._renewed_at = started

    async def _heartbeat(self) -> None:
        while not self._lost:
            await asyncio.sleep(self._ttl_seconds / _HEARTBEATS_PER_TTL)
            try:
                await self._renew()
            except AiTurnLeaseLostError:
                return
            except Exception:
                # A failed renewal is retried on the next tick. Once a full TTL has passed since the last success, the
                # database may already have expired the lease, so the lease is treated as lost.
                log.exception("Failed to renew AI turn lease key=%s", self.lease_key)
                if asyncio.get_running_loop().time() >= self._renewed_at + self._ttl_seconds:
                    self._mark_lost("renewals kept failing for a full TTL")
                    return


@asynccontextmanager
async def ai_turn_lease(
    *,
    session_factory: async_sessionmaker[AsyncSession],
    lease_key: str,
    ttl_seconds: float = AI_TURN_LEASE_TTL_SECONDS,
) -> AsyncIterator[AiTurnLease | None]:
    """Yield the held lease, or None while another live turn holds ``lease_key``.

    The lease is renewed in the background for as long as the block runs, so a slow turn with provider retries
    keeps the key. It is released on exit, and expires on its own only when the instance dies.
    """
    acquired_at = asyncio.get_running_loop().time()
    owner_token = await try_acquire_ai_turn_lease(
        session_factory=session_factory, lease_key=lease_key, ttl_seconds=ttl_seconds
    )
    if owner_token is None:
        yield None
        return
    lease = AiTurnLease(
        session_factory=session_factory,
        lease_key=lease_key,
        owner_token=owner_token,
        ttl_seconds=ttl_seconds,
        acquired_at=acquired_at,
    )
    heartbeat = asyncio.create_task(lease._heartbeat())
    try:
        yield lease
    finally:
        heartbeat.cancel()
        try:
            await release_ai_turn_lease(session_factory=session_factory, lease_key=lease_key, owner_token=owner_token)
        except Exception:
            # The lease still expires on its own, so a failed release only delays the next turn for this key.
            log.exception("Failed to release AI turn lease key=%s", lease_key)
