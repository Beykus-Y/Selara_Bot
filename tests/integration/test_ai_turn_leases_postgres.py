"""Concurrency test for the durable AI turn lease (issues #49, #50 and #143).

Hypothesis this guards against: the Personal AI and ?/?? cooldown is a check-then-act on saved history. Two
near-simultaneous messages from one user both pass the cooldown and both start a provider call from the same
context. The lease is a single `INSERT ... ON CONFLICT ... WHERE expired` statement, so Postgres lets exactly one
of several truly concurrent callers take a live key. A crashed turn's lease expires, and a finished turn releases it.

Issue #143: an owner whose heartbeat stalled past the TTL must not renew its expired lease. Once another owner takes
the key, the old turn must stop at its next checkpoint, and releasing the old lease must not free the new one.
"""

from __future__ import annotations

import asyncio
import os
from datetime import timedelta

import pytest
from sqlalchemy import func, update
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from selara.infrastructure.db.ai_turn_leases import (
    AiTurnLeaseLostError,
    ai_turn_lease,
    release_ai_turn_lease,
    renew_ai_turn_lease,
    try_acquire_ai_turn_lease,
)
from selara.infrastructure.db.base import Base
from selara.infrastructure.db.models import AiTurnLeaseModel

_TTL = 300.0


async def _session_factory():
    database_url = os.getenv("TEST_DATABASE_URL")
    if not database_url:
        pytest.skip("TEST_DATABASE_URL is not set")
    engine = create_async_engine(database_url)
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.drop_all)
        await connection.run_sync(Base.metadata.create_all)
    return engine, async_sessionmaker(engine, expire_on_commit=False)


async def _expire(session_factory: async_sessionmaker, lease_key: str) -> None:
    # What a stalled owner leaves behind: its lease is past its expiry, and its token still matches the row.
    async with session_factory() as session:
        await session.execute(
            update(AiTurnLeaseModel)
            .where(AiTurnLeaseModel.lease_key == lease_key)
            .values(lease_expires_at=func.now() - timedelta(seconds=1))
            .execution_options(synchronize_session=False)
        )
        await session.commit()


@pytest.mark.integration
@pytest.mark.asyncio
async def test_concurrent_acquires_for_one_key_grant_exactly_one_lease() -> None:
    engine, session_factory = await _session_factory()
    try:
        tokens = await asyncio.gather(
            *(
                try_acquire_ai_turn_lease(session_factory=session_factory, lease_key="personal_ai:1", ttl_seconds=_TTL)
                for _ in range(6)
            )
        )
        granted = [token for token in tokens if token is not None]
        assert len(granted) == 1
    finally:
        await engine.dispose()


@pytest.mark.integration
@pytest.mark.asyncio
async def test_held_key_blocks_until_released_and_different_keys_do_not_block() -> None:
    engine, session_factory = await _session_factory()
    try:
        first = await try_acquire_ai_turn_lease(session_factory=session_factory, lease_key="llm_admin:-1:2", ttl_seconds=_TTL)
        assert first is not None
        assert await try_acquire_ai_turn_lease(
            session_factory=session_factory, lease_key="llm_admin:-1:2", ttl_seconds=_TTL
        ) is None
        other = await try_acquire_ai_turn_lease(
            session_factory=session_factory, lease_key="llm_admin:-1:3", ttl_seconds=_TTL
        )
        assert other is not None

        await release_ai_turn_lease(session_factory=session_factory, lease_key="llm_admin:-1:2", owner_token=first)
        assert await try_acquire_ai_turn_lease(
            session_factory=session_factory, lease_key="llm_admin:-1:2", ttl_seconds=_TTL
        ) is not None
    finally:
        await engine.dispose()


@pytest.mark.integration
@pytest.mark.asyncio
async def test_expired_lease_is_taken_over_and_stale_owner_cannot_release_it() -> None:
    engine, session_factory = await _session_factory()
    try:
        # A negative TTL writes an already expired lease, the same state a crashed instance leaves behind.
        stale = await try_acquire_ai_turn_lease(session_factory=session_factory, lease_key="personal_ai:7", ttl_seconds=-60)
        assert stale is not None
        current = await try_acquire_ai_turn_lease(
            session_factory=session_factory, lease_key="personal_ai:7", ttl_seconds=_TTL
        )
        assert current is not None

        # The old owner finishing late must not drop the lease the new owner holds.
        await release_ai_turn_lease(session_factory=session_factory, lease_key="personal_ai:7", owner_token=stale)
        assert await try_acquire_ai_turn_lease(
            session_factory=session_factory, lease_key="personal_ai:7", ttl_seconds=_TTL
        ) is None
    finally:
        await engine.dispose()


@pytest.mark.integration
@pytest.mark.asyncio
async def test_context_manager_reports_busy_and_releases_on_exit() -> None:
    engine, session_factory = await _session_factory()
    try:
        async with ai_turn_lease(session_factory=session_factory, lease_key="personal_ai:9", ttl_seconds=_TTL) as held:
            assert held is not None
            async with ai_turn_lease(
                session_factory=session_factory, lease_key="personal_ai:9", ttl_seconds=_TTL
            ) as second:
                assert second is None
        async with ai_turn_lease(session_factory=session_factory, lease_key="personal_ai:9", ttl_seconds=_TTL) as again:
            assert again is not None
    finally:
        await engine.dispose()


@pytest.mark.integration
@pytest.mark.asyncio
async def test_renew_extends_only_the_current_owner() -> None:
    engine, session_factory = await _session_factory()
    try:
        token = await try_acquire_ai_turn_lease(session_factory=session_factory, lease_key="personal_ai:13", ttl_seconds=_TTL)
        assert token is not None
        assert await renew_ai_turn_lease(
            session_factory=session_factory, lease_key="personal_ai:13", owner_token=token, ttl_seconds=_TTL
        ) is True
        assert await renew_ai_turn_lease(
            session_factory=session_factory, lease_key="personal_ai:13", owner_token="not-the-owner", ttl_seconds=_TTL
        ) is False
    finally:
        await engine.dispose()


@pytest.mark.integration
@pytest.mark.asyncio
async def test_renew_does_not_revive_an_expired_lease() -> None:
    engine, session_factory = await _session_factory()
    try:
        # A stalled owner: its lease ran out, but its token still matches the row and nobody has taken it yet.
        token = await try_acquire_ai_turn_lease(
            session_factory=session_factory, lease_key="personal_ai:21", ttl_seconds=-60
        )
        assert token is not None
        assert await renew_ai_turn_lease(
            session_factory=session_factory, lease_key="personal_ai:21", owner_token=token, ttl_seconds=_TTL
        ) is False
        # The refused renewal left the lease expired, so the next caller can still take the key.
        assert await try_acquire_ai_turn_lease(
            session_factory=session_factory, lease_key="personal_ai:21", ttl_seconds=_TTL
        ) is not None
    finally:
        await engine.dispose()


@pytest.mark.integration
@pytest.mark.asyncio
async def test_confirm_refuses_an_expired_lease_even_when_nobody_took_it() -> None:
    engine, session_factory = await _session_factory()
    lease_key = "personal_ai:23"
    try:
        async with ai_turn_lease(session_factory=session_factory, lease_key=lease_key, ttl_seconds=_TTL) as lease:
            assert lease is not None
            await _expire(session_factory, lease_key)
            # Before a result is saved or published, the owner re-checks the database and must not revive its lease.
            with pytest.raises(AiTurnLeaseLostError):
                await lease.confirm()
            assert lease.lost
    finally:
        await engine.dispose()


@pytest.mark.integration
@pytest.mark.asyncio
async def test_takeover_stops_the_old_turn_at_its_next_checkpoint_and_keeps_the_new_owners_lease() -> None:
    engine, session_factory = await _session_factory()
    lease_key = "llm_admin:-7:8"
    try:
        steps: list[int] = []

        async def turn(lease) -> None:
            for step in range(500):
                await lease.confirm()
                await asyncio.sleep(0.02)
                steps.append(step)

        async def take_over() -> None:
            await asyncio.sleep(0.1)
            # The old owner's heartbeat stalled: its lease ran out and another replica took the key.
            await _expire(session_factory, lease_key)
            assert await try_acquire_ai_turn_lease(
                session_factory=session_factory, lease_key=lease_key, ttl_seconds=_TTL
            ) is not None

        async with ai_turn_lease(session_factory=session_factory, lease_key=lease_key, ttl_seconds=0.6) as lease:
            assert lease is not None
            takeover = asyncio.create_task(take_over())
            with pytest.raises(AiTurnLeaseLostError):
                await turn(lease)
            await takeover

            # The checkpoint refused the step after the takeover: the turn stopped early, the lease reports lost,
            # and nothing can be confirmed any more.
            assert lease.lost
            assert len(steps) < 500
            with pytest.raises(AiTurnLeaseLostError):
                await lease.confirm()

        # Leaving the block releases with the old token, which must not free the lease the new owner holds.
        assert await try_acquire_ai_turn_lease(
            session_factory=session_factory, lease_key=lease_key, ttl_seconds=_TTL
        ) is None
    finally:
        await engine.dispose()


@pytest.mark.integration
@pytest.mark.asyncio
async def test_heartbeat_keeps_a_long_turn_past_its_ttl() -> None:
    engine, session_factory = await _session_factory()
    try:
        # The TTL is shorter than the turn, as provider retries can make a real turn. The heartbeat must keep the key.
        async with ai_turn_lease(session_factory=session_factory, lease_key="llm_admin:-5:6", ttl_seconds=0.6) as held:
            assert held is not None
            await asyncio.sleep(1.5)
            assert not held.lost
            assert await try_acquire_ai_turn_lease(
                session_factory=session_factory, lease_key="llm_admin:-5:6", ttl_seconds=_TTL
            ) is None
    finally:
        await engine.dispose()
