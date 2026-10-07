import asyncio
import importlib.util
from datetime import datetime, timedelta, timezone

import pytest
from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from selara.core.config import Settings
from selara.infrastructure.db import message_archive_retention
from selara.infrastructure.db.base import Base
from selara.infrastructure.db.message_archive_retention import purge_expired_message_archive
from selara.infrastructure.db.models import ActivityEventDeadLetterModel, MessageArchiveModel

pytestmark = pytest.mark.skipif(importlib.util.find_spec("aiosqlite") is None, reason="aiosqlite is not installed")

NOW = datetime(2026, 10, 7, 12, 0, tzinfo=timezone.utc)
RETENTION_DAYS = 14


def _archive_row(message_id: int, *, age_days: float) -> MessageArchiveModel:
    moment = NOW - timedelta(days=age_days)
    return MessageArchiveModel(
        chat_id=9001,
        user_id=901,
        telegram_message_id=message_id,
        snapshot_kind="created",
        snapshot_at=moment,
        sent_at=moment,
        message_type="text",
        text=f"message {message_id}",
        raw_message_json={"message_id": message_id},
        snapshot_hash=f"hash-{message_id}",
    )


def _dead_letter(row_id: int, *, age_days: float) -> ActivityEventDeadLetterModel:
    moment = NOW - timedelta(days=age_days)
    return ActivityEventDeadLetterModel(
        id=row_id,
        chat_id=9001,
        chat_type="group",
        chat_title="Retention",
        payload={"telegram_message_id": row_id},
        created_at=moment,
        attempts=5,
        failed_at=moment,
    )


async def _engine_and_session_factory():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    return engine, async_sessionmaker(engine, expire_on_commit=False)


async def _seed(session_factory, *rows) -> None:
    async with session_factory() as session:
        session.add_all(rows)
        await session.commit()


async def _ids(session_factory, model) -> list[int]:
    async with session_factory() as session:
        return list((await session.execute(select(model.id).order_by(model.id))).scalars())


def _settings(**overrides) -> Settings:
    return Settings(BOT_TOKEN="123456:TESTTOKEN", DATABASE_URL="sqlite+aiosqlite:///tmp/test.db", **overrides)


@pytest.mark.asyncio
async def test_rows_older_than_the_window_are_deleted_and_recent_rows_are_kept() -> None:
    engine, session_factory = await _engine_and_session_factory()
    await _seed(
        session_factory,
        *[_archive_row(message_id, age_days=15) for message_id in (1, 2, 3)],
        *[_archive_row(message_id, age_days=13) for message_id in (4, 5)],
    )

    result = await purge_expired_message_archive(session_factory, retention_days=RETENTION_DAYS, now=NOW)

    assert result.archive_rows == 3
    assert await _ids(session_factory, MessageArchiveModel) == [4, 5]
    await engine.dispose()


@pytest.mark.asyncio
async def test_expired_rows_are_deleted_in_batches_until_none_are_left() -> None:
    engine, session_factory = await _engine_and_session_factory()
    await _seed(session_factory, *[_archive_row(message_id, age_days=30) for message_id in range(1, 6)])

    result = await purge_expired_message_archive(
        session_factory,
        retention_days=RETENTION_DAYS,
        now=NOW,
        batch_size=2,
    )

    assert result.archive_rows == 5
    assert await _ids(session_factory, MessageArchiveModel) == []
    await engine.dispose()


@pytest.mark.asyncio
async def test_parked_dead_letters_follow_the_same_window() -> None:
    engine, session_factory = await _engine_and_session_factory()
    await _seed(session_factory, _dead_letter(101, age_days=20), _dead_letter(102, age_days=2))

    result = await purge_expired_message_archive(session_factory, retention_days=RETENTION_DAYS, now=NOW)

    assert result.dead_letters == 1
    assert await _ids(session_factory, ActivityEventDeadLetterModel) == [102]
    await engine.dispose()


@pytest.mark.asyncio
async def test_zero_retention_turns_the_purge_off() -> None:
    engine, session_factory = await _engine_and_session_factory()
    await _seed(session_factory, _archive_row(1, age_days=365), _dead_letter(101, age_days=365))

    result = await purge_expired_message_archive(session_factory, retention_days=0, now=NOW)

    assert result.archive_rows == 0
    assert result.dead_letters == 0
    assert await _ids(session_factory, MessageArchiveModel) == [1]
    assert await _ids(session_factory, ActivityEventDeadLetterModel) == [101]
    await engine.dispose()


@pytest.mark.asyncio
async def test_disabled_scheduler_returns_at_once() -> None:
    await asyncio.wait_for(
        message_archive_retention.run_message_archive_retention_scheduler(session_factory=None, retention_days=0),
        timeout=1,
    )


@pytest.mark.asyncio
async def test_scheduler_keeps_running_after_a_failed_run(monkeypatch) -> None:
    calls: list[int] = []

    async def _flaky_purge(session_factory, *, retention_days, pause_seconds=0.0, **_):
        calls.append(retention_days)
        if len(calls) == 1:
            raise RuntimeError("database is unavailable")
        return message_archive_retention.MessageArchivePurgeResult()

    monkeypatch.setattr(message_archive_retention, "FIRST_RUN_DELAY_SECONDS", 0)
    monkeypatch.setattr(message_archive_retention, "purge_expired_message_archive", _flaky_purge)
    task = asyncio.create_task(
        message_archive_retention.run_message_archive_retention_scheduler(
            session_factory=None,
            retention_days=RETENTION_DAYS,
            interval_seconds=0,
        )
    )

    async def _until_second_run() -> None:
        while len(calls) < 2:
            await asyncio.sleep(0)

    await asyncio.wait_for(_until_second_run(), timeout=5)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task


@pytest.mark.parametrize("days", [1, 6])
def test_retention_window_must_be_off_or_at_least_a_week(days: int) -> None:
    with pytest.raises(ValidationError):
        _settings(MESSAGE_ARCHIVE_RETENTION_DAYS=days)


@pytest.mark.parametrize("days", [0, 7, 14])
def test_retention_window_accepts_off_and_a_week_or_more(days: int) -> None:
    assert _settings(MESSAGE_ARCHIVE_RETENTION_DAYS=days).message_archive_retention_days == days


def test_retention_is_off_by_default() -> None:
    # Reads the declared default, so a local .env that sets the variable cannot change the result.
    assert Settings.model_fields["message_archive_retention_days"].default == 0
