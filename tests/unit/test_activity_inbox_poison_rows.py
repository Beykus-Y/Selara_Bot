import importlib.util
import logging
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from sqlalchemy import func, select
from sqlalchemy.exc import OperationalError
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from selara.application.achievements import AchievementCatalogService
from selara.infrastructure.db import activity_batcher as activity_batcher_module
from selara.infrastructure.db.activity_batcher import ActivityBatcher
from selara.infrastructure.db.activity_inbox import retarget_activity_inbox_chat
from selara.infrastructure.db.base import Base
from selara.infrastructure.db.models import (
    ActivityEventDeadLetterModel,
    ActivityEventInboxModel,
    MessageArchiveModel,
)
from selara.infrastructure.db.repositories import SqlAlchemyActivityRepository

pytestmark = pytest.mark.skipif(importlib.util.find_spec("aiosqlite") is None, reason="aiosqlite is not installed")

POISON_MESSAGE_ID = 3
_SENT_AT = datetime(2026, 3, 13, 15, 0, tzinfo=timezone.utc)


def _catalog() -> AchievementCatalogService:
    return AchievementCatalogService.load(Path("src/selara/core/achievements.json"))


def _message(*, chat_id: int, user_id: int, message_id: int) -> dict[str, object]:
    return {
        "chat_id": chat_id,
        "chat_type": "group",
        "chat_title": "Poison",
        "user_id": user_id,
        "username": "frank",
        "first_name": "Frank",
        "last_name": None,
        "is_bot": False,
        "event_at": _SENT_AT,
        "telegram_message_id": message_id,
        "snapshot_kind": "created",
        "snapshot_at": _SENT_AT,
        "sent_at": _SENT_AT,
        "message_type": "text",
        "text": f"message {message_id}",
        "raw_message_json": {"message_id": message_id},
        "snapshot_hash": f"hash-{message_id}",
    }


def _batcher(session_factory) -> ActivityBatcher:
    return ActivityBatcher(
        session_factory=session_factory,
        catalog=_catalog(),
        flush_seconds=60,
        max_events=1000,
        max_row_attempts=3,
    )


async def _flusher_run(session_factory) -> None:
    """One flusher run, started and stopped the way a process restart does."""
    batcher = _batcher(session_factory)
    await batcher.start()
    await batcher.close()


async def _count(session_factory, model) -> int:
    async with session_factory() as session:
        return int((await session.execute(select(func.count()).select_from(model))).scalar_one())


async def _inbox_attempts(session_factory) -> list[int]:
    async with session_factory() as session:
        result = await session.execute(select(ActivityEventInboxModel.attempts).order_by(ActivityEventInboxModel.id))
        return list(result.scalars())


async def _message_count(session_factory, *, chat_id: int, user_id: int) -> int:
    async with session_factory() as session:
        stats = await SqlAlchemyActivityRepository(session).get_user_stats(chat_id=chat_id, user_id=user_id)
    return 0 if stats is None else stats.message_count


@pytest.mark.asyncio
async def test_a_row_that_always_fails_is_parked_and_the_rest_of_the_inbox_keeps_flowing(monkeypatch, caplog) -> None:
    caplog.set_level(logging.WARNING, logger="selara.infrastructure.db.activity_batcher")
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    original = SqlAlchemyActivityRepository.flush_activity_batch

    async def _reject_poison_message(self, events):
        if any(event.telegram_message_id == POISON_MESSAGE_ID for event in events):
            raise ValueError("this message can never be applied")
        return await original(self, events)

    monkeypatch.setattr(SqlAlchemyActivityRepository, "flush_activity_batch", _reject_poison_message)

    enqueue = _batcher(session_factory)
    for chat_id, user_id, message_id in [
        (7001, 701, 1),
        (7001, 701, 2),
        (7001, 701, POISON_MESSAGE_ID),
        (7001, 701, 4),
        (7002, 702, 5),
    ]:
        await enqueue.enqueue_message(**_message(chat_id=chat_id, user_id=user_id, message_id=message_id))

    # The first run applies the good rows on both sides of the poison row, and counts one failed attempt.
    await _flusher_run(session_factory)
    assert await _inbox_attempts(session_factory) == [1]
    assert await _message_count(session_factory, chat_id=7001, user_id=701) == 3
    assert await _message_count(session_factory, chat_id=7002, user_id=702) == 1

    await _flusher_run(session_factory)
    assert await _inbox_attempts(session_factory) == [2]

    # The third failed attempt parks the row, so the inbox is empty and the failure is logged.
    await _flusher_run(session_factory)
    assert await _count(session_factory, ActivityEventInboxModel) == 0
    async with session_factory() as session:
        dead = (await session.execute(select(ActivityEventDeadLetterModel))).scalar_one()
    assert dead.attempts == 3
    assert dead.chat_id == 7001
    assert dead.payload["telegram_message_id"] == POISON_MESSAGE_ID
    parked_logs = [
        record
        for record in caplog.records
        if record.levelno == logging.ERROR and getattr(record, "inbox_row_id", None) == dead.id
    ]
    assert len(parked_logs) == 1

    # Rows that arrive after the parking keep flowing.
    await enqueue.enqueue_message(**_message(chat_id=7001, user_id=701, message_id=6))
    await _flusher_run(session_factory)
    assert await _message_count(session_factory, chat_id=7001, user_id=701) == 4
    assert await _count(session_factory, ActivityEventInboxModel) == 0
    await engine.dispose()


@pytest.mark.asyncio
async def test_a_database_outage_never_parks_inbox_rows(monkeypatch) -> None:
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    enqueue = _batcher(session_factory)
    for message_id in range(1, 4):
        await enqueue.enqueue_message(**_message(chat_id=7101, user_id=711, message_id=message_id))

    original_flush = SqlAlchemyActivityRepository.flush_activity_batch
    original_record = activity_batcher_module.record_activity_inbox_failure

    async def _database_down(*args, **kwargs):
        raise OperationalError("UPDATE activity_event_inbox", {}, ConnectionError("database is down"))

    monkeypatch.setattr(SqlAlchemyActivityRepository, "flush_activity_batch", _database_down)
    monkeypatch.setattr(activity_batcher_module, "record_activity_inbox_failure", _database_down)

    # Well past the attempt limit. A database that is down must not count against the rows.
    for _ in range(5):
        await _flusher_run(session_factory)
        assert await _inbox_attempts(session_factory) == [0, 0, 0]
    assert await _count(session_factory, ActivityEventDeadLetterModel) == 0

    monkeypatch.setattr(SqlAlchemyActivityRepository, "flush_activity_batch", original_flush)
    monkeypatch.setattr(activity_batcher_module, "record_activity_inbox_failure", original_record)
    await _flusher_run(session_factory)
    assert await _count(session_factory, ActivityEventInboxModel) == 0
    assert await _count(session_factory, MessageArchiveModel) == 3
    await engine.dispose()


@pytest.mark.asyncio
async def test_a_row_that_cannot_be_decoded_is_parked_without_holding_back_good_rows() -> None:
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    async with session_factory() as session:
        session.add(ActivityEventInboxModel(chat_id=7201, chat_type="group", chat_title="Undecodable", payload={}))
        await session.commit()
    enqueue = _batcher(session_factory)
    await enqueue.enqueue_message(**_message(chat_id=7202, user_id=721, message_id=10))

    await _flusher_run(session_factory)
    assert await _message_count(session_factory, chat_id=7202, user_id=721) == 1
    assert await _inbox_attempts(session_factory) == [1]

    await _flusher_run(session_factory)
    await _flusher_run(session_factory)
    assert await _count(session_factory, ActivityEventInboxModel) == 0
    async with session_factory() as session:
        dead = (await session.execute(select(ActivityEventDeadLetterModel))).scalar_one()
    assert dead.chat_id == 7201
    assert dead.attempts == 3
    await engine.dispose()


@pytest.mark.asyncio
async def test_backlog_log_reports_its_size_and_the_age_of_its_oldest_row(caplog) -> None:
    caplog.set_level(logging.INFO, logger="selara.infrastructure.db.activity_batcher")
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    now = datetime.now(timezone.utc)
    async with session_factory() as session:
        session.add_all(
            [
                ActivityEventInboxModel(
                    chat_id=7301, chat_type="group", chat_title="Backlog", payload={}, created_at=now - timedelta(hours=2)
                ),
                ActivityEventInboxModel(chat_id=7301, chat_type="group", chat_title="Backlog", payload={}, created_at=now),
            ]
        )
        await session.commit()

    await _batcher(session_factory)._log_backlog()

    (record,) = [item for item in caplog.records if item.getMessage() == "Activity inbox backlog"]
    assert record.levelno == logging.WARNING
    assert record.inbox_pending == 2
    assert record.inbox_oldest_age_seconds >= 7200
    assert record.inbox_dead_letters == 0
    await engine.dispose()


@pytest.mark.asyncio
async def test_a_chat_migration_moves_dead_letters_to_the_new_chat() -> None:
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    now = datetime.now(timezone.utc)
    async with session_factory() as session:
        session.add(
            ActivityEventDeadLetterModel(
                id=1,
                chat_id=7401,
                chat_type="group",
                chat_title="Before",
                payload={},
                created_at=now,
                attempts=3,
                failed_at=now,
            )
        )
        await session.commit()
        await retarget_activity_inbox_chat(
            session,
            old_chat_id=7401,
            new_chat_id=-1007401,
            chat_type="supergroup",
            chat_title="After",
        )
        await session.commit()

    async with session_factory() as session:
        dead = (await session.execute(select(ActivityEventDeadLetterModel))).scalar_one()
    assert (dead.chat_id, dead.chat_type, dead.chat_title) == (-1007401, "supergroup", "After")
    await engine.dispose()
