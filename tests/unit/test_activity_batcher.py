import asyncio
import importlib.util
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import AsyncMock

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from selara.application.achievements import AchievementCatalogService
from selara.infrastructure.db.activity_batcher import ActivityBatcher
from selara.infrastructure.db.base import Base
from selara.infrastructure.db.models import ActivityEventInboxModel, MessageArchiveModel
from selara.infrastructure.db.repositories import SqlAlchemyActivityRepository

pytestmark = pytest.mark.skipif(importlib.util.find_spec("aiosqlite") is None, reason="aiosqlite is not installed")


def _catalog() -> AchievementCatalogService:
    return AchievementCatalogService.load(Path("src/selara/core/achievements.json"))


def _archived_message(*, chat_id: int, user_id: int, message_id: int) -> dict[str, object]:
    sent_at = datetime(2026, 3, 13, 15, 0, tzinfo=timezone.utc)
    return {
        "chat_id": chat_id,
        "chat_type": "group",
        "chat_title": "Recovery",
        "user_id": user_id,
        "username": "erin",
        "first_name": "Erin",
        "last_name": None,
        "is_bot": False,
        "event_at": sent_at,
        "telegram_message_id": message_id,
        "snapshot_kind": "created",
        "snapshot_at": sent_at,
        "sent_at": sent_at,
        "message_type": "text",
        "text": f"message {message_id}",
        "raw_message_json": {"message_id": message_id, "text": f"message {message_id}"},
        "snapshot_hash": f"hash-{message_id}",
    }


async def _count(session_factory, model) -> int:
    async with session_factory() as session:
        return int((await session.execute(select(func.count()).select_from(model))).scalar_one())


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
async def test_events_enqueued_before_a_crash_are_flushed_by_the_next_process() -> None:
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    session_factory = async_sessionmaker(engine, expire_on_commit=False)

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    # The first batcher never reaches start() or close(): the process dies after enqueue and before any flush.
    crashed = ActivityBatcher(
        session_factory=session_factory,
        catalog=_catalog(),
        flush_seconds=60,
        max_events=1000,
    )
    for message_id in range(1, 6):
        await crashed.enqueue_message(
            chat_id=5005,
            chat_type="group",
            chat_title="Crash",
            user_id=901,
            username="dave",
            first_name="Dave",
            last_name=None,
            is_bot=False,
            event_at=datetime(2026, 3, 13, 15, 0, tzinfo=timezone.utc),
            telegram_message_id=message_id,
            snapshot_kind="created",
            snapshot_at=datetime(2026, 3, 13, 15, 0, tzinfo=timezone.utc),
            sent_at=datetime(2026, 3, 13, 15, 0, tzinfo=timezone.utc),
            message_type="text",
            text=f"message {message_id}",
            raw_message_json={"message_id": message_id, "text": f"message {message_id}"},
            snapshot_hash=f"hash-{message_id}",
        )
    del crashed

    restarted = ActivityBatcher(
        session_factory=session_factory,
        catalog=_catalog(),
        flush_seconds=60,
        max_events=1000,
    )
    await restarted.start()
    await restarted.close()

    async with session_factory() as session:
        repo = SqlAlchemyActivityRepository(session)
        stats = await repo.get_user_stats(chat_id=5005, user_id=901)
        archived_rows = (
            await session.execute(
                select(func.count()).select_from(MessageArchiveModel).where(MessageArchiveModel.chat_id == 5005)
            )
        ).scalar_one()

    assert stats is not None
    assert stats.message_count == 5
    assert archived_rows == 5
    await engine.dispose()


@pytest.mark.asyncio
async def test_failed_batch_stays_in_the_inbox_and_is_applied_once_after_restart(monkeypatch) -> None:
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    session_factory = async_sessionmaker(engine, expire_on_commit=False)

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    original = SqlAlchemyActivityRepository.flush_activity_batch

    async def _crash_before_commit(self, events):
        # Stage the real writes, then fail before the batch transaction commits.
        await original(self, events)
        raise RuntimeError("simulated crash before commit")

    monkeypatch.setattr(SqlAlchemyActivityRepository, "flush_activity_batch", _crash_before_commit)

    interrupted = ActivityBatcher(
        session_factory=session_factory,
        catalog=_catalog(),
        flush_seconds=60,
        max_events=1000,
    )
    for message_id in range(1, 4):
        await interrupted.enqueue_message(**_archived_message(chat_id=6006, user_id=902, message_id=message_id))
    await interrupted.start()
    await interrupted.close()

    assert await _count(session_factory, ActivityEventInboxModel) == 3
    assert await _count(session_factory, MessageArchiveModel) == 0
    async with session_factory() as session:
        assert await SqlAlchemyActivityRepository(session).get_user_stats(chat_id=6006, user_id=902) is None

    monkeypatch.setattr(SqlAlchemyActivityRepository, "flush_activity_batch", original)
    restarted = ActivityBatcher(session_factory=session_factory, catalog=_catalog(), flush_seconds=60, max_events=1000)
    await restarted.start()
    await restarted.close()

    assert await _count(session_factory, ActivityEventInboxModel) == 0
    assert await _count(session_factory, MessageArchiveModel) == 3
    async with session_factory() as session:
        stats = await SqlAlchemyActivityRepository(session).get_user_stats(chat_id=6006, user_id=902)
    assert stats is not None
    assert stats.message_count == 3
    await engine.dispose()


@pytest.mark.asyncio
async def test_duplicate_deliveries_of_one_message_are_applied_once() -> None:
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    session_factory = async_sessionmaker(engine, expire_on_commit=False)

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    batcher = ActivityBatcher(session_factory=session_factory, catalog=_catalog(), flush_seconds=60, max_events=1000)
    # Two inbox rows for the same Telegram message, as after a redelivered update.
    for _ in range(2):
        await batcher.enqueue_message(**_archived_message(chat_id=7007, user_id=903, message_id=9))
    await batcher.start()
    await batcher.close()

    assert await _count(session_factory, ActivityEventInboxModel) == 0
    assert await _count(session_factory, MessageArchiveModel) == 1
    async with session_factory() as session:
        stats = await SqlAlchemyActivityRepository(session).get_user_stats(chat_id=7007, user_id=903)
    assert stats is not None
    assert stats.message_count == 1
    await engine.dispose()


@pytest.mark.asyncio
async def test_activity_batcher_limits_concurrent_inbox_writes() -> None:
    release = asyncio.Event()
    commits_started = 0

    class FakeSession:
        def add_all(self, rows) -> None:
            _ = rows

        async def commit(self) -> None:
            nonlocal commits_started
            commits_started += 1
            await release.wait()

    class FakeSessionFactory:
        def __call__(self):
            class Manager:
                async def __aenter__(self):
                    return FakeSession()

                async def __aexit__(self, exc_type, exc, tb):
                    return False

            return Manager()

    batcher = ActivityBatcher(
        session_factory=FakeSessionFactory(),
        catalog=_catalog(),
        flush_seconds=60,
        max_events=10,
        max_inflight_writes=1,
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
    first = asyncio.create_task(batcher.enqueue_message(**event, telegram_message_id=1))
    second = asyncio.create_task(batcher.enqueue_message(**event, telegram_message_id=2))
    await asyncio.sleep(0)
    assert commits_started == 1
    assert not second.done()

    release.set()
    await asyncio.wait_for(asyncio.gather(first, second), timeout=1)
    assert commits_started == 2


@pytest.mark.asyncio
async def test_enqueue_after_close_is_rejected() -> None:
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    session_factory = async_sessionmaker(engine, expire_on_commit=False)

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    batcher = ActivityBatcher(session_factory=session_factory, catalog=_catalog(), flush_seconds=60, max_events=10)
    await batcher.start()
    await batcher.close()

    with pytest.raises(RuntimeError, match="closed"):
        await batcher.enqueue_message(**_archived_message(chat_id=8008, user_id=904, message_id=1))
    assert await _count(session_factory, ActivityEventInboxModel) == 0
    await engine.dispose()
