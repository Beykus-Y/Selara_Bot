"""Concurrency test for the durable AI turn lease (issues #49 and #50).

Hypothesis this guards against: the Personal AI and ?/?? cooldown is a check-then-act on saved history. Two
near-simultaneous messages from one user both pass the cooldown and both start a provider call from the same
context. The lease is a single `INSERT ... ON CONFLICT ... WHERE expired` statement, so Postgres lets exactly one
of several truly concurrent callers take a live key. A crashed turn's lease expires, and a finished turn releases it.
"""

from __future__ import annotations

import asyncio
import os

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from selara.infrastructure.db.ai_turn_leases import (
    ai_turn_lease,
    release_ai_turn_lease,
    renew_ai_turn_lease,
    try_acquire_ai_turn_lease,
)
from selara.infrastructure.db.base import Base

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
            assert held is True
            async with ai_turn_lease(
                session_factory=session_factory, lease_key="personal_ai:9", ttl_seconds=_TTL
            ) as second:
                assert second is False
        async with ai_turn_lease(session_factory=session_factory, lease_key="personal_ai:9", ttl_seconds=_TTL) as again:
            assert again is True
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
async def test_heartbeat_keeps_a_long_turn_past_its_ttl() -> None:
    engine, session_factory = await _session_factory()
    try:
        # The TTL is shorter than the turn, as provider retries can make a real turn. The heartbeat must keep the key.
        async with ai_turn_lease(session_factory=session_factory, lease_key="llm_admin:-5:6", ttl_seconds=0.6) as held:
            assert held is True
            await asyncio.sleep(1.5)
            assert await try_acquire_ai_turn_lease(
                session_factory=session_factory, lease_key="llm_admin:-5:6", ttl_seconds=_TTL
            ) is None
    finally:
        await engine.dispose()
