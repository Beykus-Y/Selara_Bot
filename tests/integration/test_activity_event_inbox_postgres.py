"""Crash recovery for the activity inbox (issue #91).

A message accepted by `ActivityBatcher.enqueue_message()` must reach the activity counters and the message archive
exactly once, even if the process dies before its batch is flushed or a flush fails before its commit. The claim
uses `FOR UPDATE SKIP LOCKED`, which only PostgreSQL supports, so these checks need a real database.
"""

from __future__ import annotations

import asyncio
import os
from datetime import datetime, timezone
from pathlib import Path

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from selara.application.achievements import AchievementCatalogService
from selara.infrastructure.db.activity_batcher import ActivityBatcher
from selara.infrastructure.db.base import Base
from selara.infrastructure.db.models import ActivityEventInboxModel, MessageArchiveModel
from selara.infrastructure.db.repositories import SqlAlchemyActivityRepository

_CHAT_ID = -1009001
_USER_ID = 9001
_SENT_AT = datetime(2026, 10, 7, 12, 0, tzinfo=timezone.utc)


async def _session_factory():
    database_url = os.getenv("TEST_DATABASE_URL")
    if not database_url:
        pytest.skip("TEST_DATABASE_URL is not set")
    engine = create_async_engine(database_url)
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.drop_all)
        await connection.run_sync(Base.metadata.create_all)
    return engine, async_sessionmaker(engine, expire_on_commit=False)


def _batcher(session_factory, *, max_events: int = 1000) -> ActivityBatcher:
    return ActivityBatcher(
        session_factory=session_factory,
        catalog=AchievementCatalogService.load(Path("src/selara/core/achievements.json")),
        flush_seconds=60,
        max_events=max_events,
    )


async def _enqueue_messages(batcher: ActivityBatcher, message_ids: range) -> None:
    for message_id in message_ids:
        await batcher.enqueue_message(
            chat_id=_CHAT_ID,
            chat_type="supergroup",
            chat_title="Inbox",
            user_id=_USER_ID,
            username="ivan",
            first_name="Ivan",
            last_name=None,
            is_bot=False,
            event_at=_SENT_AT,
            telegram_message_id=message_id,
            snapshot_kind="created",
            snapshot_at=_SENT_AT,
            sent_at=_SENT_AT,
            message_type="text",
            text=f"message {message_id}",
            raw_message_json={"message_id": message_id, "text": f"message {message_id}"},
            snapshot_hash=f"hash-{message_id}",
        )


async def _totals(session_factory) -> tuple[int, int, int]:
    """Return (activity message_count, archived rows, rows still waiting in the inbox)."""
    async with session_factory() as session:
        stats = await SqlAlchemyActivityRepository(session).get_user_stats(chat_id=_CHAT_ID, user_id=_USER_ID)
        archived = (
            await session.execute(
                select(func.count()).select_from(MessageArchiveModel).where(MessageArchiveModel.chat_id == _CHAT_ID)
            )
        ).scalar_one()
        pending = (await session.execute(select(func.count()).select_from(ActivityEventInboxModel))).scalar_one()
    return (stats.message_count if stats is not None else 0), int(archived), int(pending)


@pytest.mark.integration
@pytest.mark.asyncio
async def test_unfinished_batch_survives_a_crash_and_is_applied_exactly_once(monkeypatch) -> None:
    engine, session_factory = await _session_factory()
    try:
        # The process dies right after enqueue. This batcher never starts, so nothing is flushed.
        dead = _batcher(session_factory)
        await _enqueue_messages(dead, range(1, 11))
        del dead

        # The next process starts, but its flush fails after staging writes and before the commit.
        original = SqlAlchemyActivityRepository.flush_activity_batch

        async def _crash_before_commit(self, events):
            await original(self, events)
            raise RuntimeError("simulated crash before commit")

        monkeypatch.setattr(SqlAlchemyActivityRepository, "flush_activity_batch", _crash_before_commit)
        crashing = _batcher(session_factory)
        await crashing.start()
        await crashing.close()
        assert await _totals(session_factory) == (0, 0, 10)

        monkeypatch.setattr(SqlAlchemyActivityRepository, "flush_activity_batch", original)
        recovered = _batcher(session_factory)
        await recovered.start()
        await recovered.close()
        assert await _totals(session_factory) == (10, 10, 0)

        # Redelivering the same messages after recovery must not count them again.
        redelivered = _batcher(session_factory)
        await _enqueue_messages(redelivered, range(1, 11))
        await redelivered.start()
        await redelivered.close()
        assert await _totals(session_factory) == (10, 10, 0)
    finally:
        await engine.dispose()


@pytest.mark.integration
@pytest.mark.asyncio
async def test_concurrent_flushers_claim_disjoint_inbox_rows() -> None:
    engine, session_factory = await _session_factory()
    try:
        producer = _batcher(session_factory)
        await _enqueue_messages(producer, range(1, 41))

        # Small batches make the two flushers interleave. SKIP LOCKED keeps them from claiming the same rows.
        flushers = [_batcher(session_factory, max_events=7) for _ in range(2)]
        for flusher in flushers:
            await flusher.start()
        await asyncio.gather(*(flusher.close() for flusher in flushers))

        # A batch that failed under contention stays in the inbox. One more pass applies it.
        leftover = _batcher(session_factory)
        await leftover.start()
        await leftover.close()

        assert await _totals(session_factory) == (40, 40, 0)
    finally:
        await engine.dispose()
