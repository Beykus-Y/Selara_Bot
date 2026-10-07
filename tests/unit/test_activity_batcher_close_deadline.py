import asyncio
import importlib.util
import logging
import time
from datetime import datetime, timezone
from pathlib import Path

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from selara.application.achievements import AchievementCatalogService
from selara.infrastructure.db.activity_batcher import ActivityBatcher
from selara.infrastructure.db.base import Base
from selara.infrastructure.db.models import ActivityEventInboxModel
from selara.infrastructure.db.repositories import SqlAlchemyActivityRepository

pytestmark = pytest.mark.skipif(importlib.util.find_spec("aiosqlite") is None, reason="aiosqlite is not installed")

_SENT_AT = datetime(2026, 3, 13, 15, 0, tzinfo=timezone.utc)


def _catalog() -> AchievementCatalogService:
    return AchievementCatalogService.load(Path("src/selara/core/achievements.json"))


def _event(message_id: int) -> dict[str, object]:
    return {
        "chat_id": 8001,
        "chat_type": "group",
        "chat_title": "Close",
        "user_id": 801,
        "username": "grace",
        "first_name": "Grace",
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


async def _engine_and_session_factory():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    return engine, async_sessionmaker(engine, expire_on_commit=False)


@pytest.mark.asyncio
async def test_close_returns_when_a_flush_never_finishes(monkeypatch, caplog) -> None:
    caplog.set_level(logging.ERROR, logger="selara.infrastructure.db.activity_batcher")
    engine, session_factory = await _engine_and_session_factory()

    async def _stuck_flush(self, events):
        # Stands in for a database call that never answers, such as a connection to a dead server.
        await asyncio.Event().wait()

    monkeypatch.setattr(SqlAlchemyActivityRepository, "flush_activity_batch", _stuck_flush)
    batcher = ActivityBatcher(
        session_factory=session_factory,
        catalog=_catalog(),
        flush_seconds=60,
        max_events=1000,
        close_grace_seconds=0.2,
    )
    await batcher.start()
    await batcher.enqueue_message(**_event(1))

    started = time.monotonic()
    await asyncio.wait_for(batcher.close(), timeout=5)
    assert time.monotonic() - started < 3

    # The stuck batch never committed, so its event is still in the inbox for the next start, and shutdown logs it.
    async with session_factory() as session:
        pending = await session.scalar(select(func.count()).select_from(ActivityEventInboxModel))
    assert pending == 1
    unflushed = [record for record in caplog.records if "not fully applied" in record.getMessage()]
    assert len(unflushed) == 1
    assert unflushed[0].inbox_pending == 1
    await engine.dispose()


@pytest.mark.asyncio
async def test_close_applies_the_inbox_and_releases_every_write_slot() -> None:
    engine, session_factory = await _engine_and_session_factory()
    batcher = ActivityBatcher(
        session_factory=session_factory,
        catalog=_catalog(),
        flush_seconds=60,
        max_events=1000,
        max_inflight_writes=2,
    )
    await batcher.start()
    for message_id in (1, 2, 3):
        await batcher.enqueue_message(**_event(message_id))

    await asyncio.wait_for(batcher.close(), timeout=10)

    async with session_factory() as session:
        pending = await session.scalar(select(func.count()).select_from(ActivityEventInboxModel))
        stats = await SqlAlchemyActivityRepository(session).get_user_stats(chat_id=8001, user_id=801)
    assert pending == 0
    assert stats is not None and stats.message_count == 3
    assert batcher._write_slots._value == 2
    await engine.dispose()
