import asyncio
import importlib.util
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import AsyncMock

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from selara.application.achievements import AchievementCatalogService
from selara.infrastructure.db import activity_batcher as activity_batcher_module
from selara.infrastructure.db.activity_batcher import ActivityBatcher
from selara.infrastructure.db.activity_batching import ActivityBatchFlushResult
from selara.infrastructure.db.base import Base
from selara.infrastructure.db.repositories import SqlAlchemyActivityRepository

pytestmark = pytest.mark.skipif(importlib.util.find_spec("aiosqlite") is None, reason="aiosqlite is not installed")


def _catalog() -> AchievementCatalogService:
    return AchievementCatalogService.load(Path("src/selara/core/achievements.json"))


@pytest.mark.asyncio
async def test_activity_batcher_close_flushes_tail_and_awards_batched_achievements() -> None:
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    publisher = AsyncMock()

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    batcher = ActivityBatcher(
        session_factory=session_factory,
        catalog=_catalog(),
        flush_seconds=60,
        max_events=1000,
        live_event_publisher=publisher,
    )
    await batcher.start()

    for message_id in range(1, 101):
        await batcher.enqueue_message(
            chat_id=1001,
            chat_type="group",
            chat_title="Batch",
            user_id=501,
            username="alice",
            first_name="Alice",
            last_name=None,
            is_bot=False,
            event_at=datetime(2026, 3, 13, 12, 0, tzinfo=timezone.utc),
            telegram_message_id=message_id,
        )

    await batcher.close()

    async with session_factory() as session:
        repo = SqlAlchemyActivityRepository(session)
        chat_achievements = await repo.list_user_chat_achievements(chat_id=1001, user_id=501)
        global_achievements = await repo.list_user_global_achievements(user_id=501)

        assert {item.achievement_id for item in chat_achievements} >= {
            "first_message",
            "chat_100_messages",
            "chat_100_messages_day",
        }
        assert {item.achievement_id for item in global_achievements} >= {"global_3_achievements"}

    publisher.assert_awaited_once_with(event_type="chat_activity", scope="chat", chat_id=1001)
    await engine.dispose()


@pytest.mark.asyncio
async def test_activity_batcher_retries_failed_flush_without_losing_events(monkeypatch) -> None:
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    session_factory = async_sessionmaker(engine, expire_on_commit=False)

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    original = SqlAlchemyActivityRepository.flush_activity_batch
    calls = {"count": 0}

    async def _flaky_flush(self, events):
        calls["count"] += 1
        if calls["count"] == 1:
            raise RuntimeError("temporary failure")
        return await original(self, events)

    monkeypatch.setattr(SqlAlchemyActivityRepository, "flush_activity_batch", _flaky_flush)

    batcher = ActivityBatcher(
        session_factory=session_factory,
        catalog=_catalog(),
        flush_seconds=1,
        max_events=1,
    )
    await batcher.start()
    await batcher.enqueue_message(
        chat_id=2002,
        chat_type="group",
        chat_title="Retry",
        user_id=601,
        username="bob",
        first_name="Bob",
        last_name=None,
        is_bot=False,
        event_at=datetime(2026, 3, 13, 13, 0, tzinfo=timezone.utc),
        telegram_message_id=42,
    )

    await asyncio.sleep(0.1)
    await batcher.close()

    async with session_factory() as session:
        repo = SqlAlchemyActivityRepository(session)
        stats = await repo.get_user_stats(chat_id=2002, user_id=601)
        assert stats is not None
        assert stats.message_count == 1

    assert calls["count"] >= 2
    await engine.dispose()


@pytest.mark.asyncio
async def test_activity_batcher_applies_backpressure_at_capacity(monkeypatch) -> None:
    class FakeSession:
        async def commit(self) -> None:
            return None

    class FakeSessionFactory:
        def __call__(self):
            class Manager:
                async def __aenter__(self):
                    return FakeSession()

                async def __aexit__(self, exc_type, exc, tb):
                    return False

            return Manager()

    class FakeRepo:
        def __init__(self, session) -> None:
            _ = session

        async def flush_activity_batch(self, batch):
            _ = batch
            return ActivityBatchFlushResult()

    monkeypatch.setattr(activity_batcher_module, "SqlAlchemyActivityRepository", FakeRepo)

    batcher = ActivityBatcher(
        session_factory=FakeSessionFactory(),
        catalog=_catalog(),
        flush_seconds=60,
        max_events=10,
        max_pending_events=1,
    )
    event = {
        "chat_id": 3003,
        "chat_type": "group",
        "chat_title": "Capacity",
        "user_id": 701,
        "username": "carol",
        "first_name": "Carol",
        "last_name": None,
        "is_bot": False,
        "event_at": datetime(2026, 3, 13, 14, 0, tzinfo=timezone.utc),
    }
    await batcher.enqueue_message(**event, telegram_message_id=1)
    blocked_enqueue = asyncio.create_task(
        batcher.enqueue_message(**event, telegram_message_id=2)
    )
    await asyncio.sleep(0)
    assert not blocked_enqueue.done()

    first_batch = await batcher._drain_pending()
    assert await batcher._flush_batch(first_batch)
    await asyncio.wait_for(blocked_enqueue, timeout=1)

    second_batch = await batcher._drain_pending()
    assert await batcher._flush_batch(second_batch)
