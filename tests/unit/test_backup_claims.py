from __future__ import annotations

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from selara.infrastructure import backup
from selara.infrastructure.db.backup_claims import (
    BACKUP_SLOT_COMPLETED,
    BACKUP_SLOT_FAILED,
    finish_backup_slot,
    renew_backup_slot_lease,
    try_claim_backup_slot,
)
from selara.infrastructure.db.base import Base
from selara.infrastructure.db.models import BackupJobClaimModel

SLOT = "daily:2026-10-08"
LEASE_SECONDS = 15 * 60
START = datetime(2026, 10, 8, 0, 5, tzinfo=timezone.utc)


async def _session_factory():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    return engine, async_sessionmaker(engine, expire_on_commit=False)


async def _claim(session_factory, owner: str, at: datetime) -> bool:
    return await try_claim_backup_slot(
        session_factory=session_factory,
        slot_key=SLOT,
        owner_token=owner,
        lease_seconds=LEASE_SECONDS,
        now=at,
    )


async def _load(session_factory) -> BackupJobClaimModel:
    async with session_factory() as session:
        row = await session.get(BackupJobClaimModel, SLOT)
        assert row is not None
        return row


@pytest.mark.asyncio
async def test_second_instance_cannot_claim_a_live_slot():
    engine, session_factory = await _session_factory()
    try:
        assert await _claim(session_factory, "instance-a", START) is True
        assert await _claim(session_factory, "instance-b", START + timedelta(minutes=1)) is False
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_expired_lease_is_reclaimed_and_previous_owner_loses_the_slot():
    engine, session_factory = await _session_factory()
    try:
        assert await _claim(session_factory, "instance-a", START) is True

        takeover_at = START + timedelta(seconds=LEASE_SECONDS + 1)
        assert await _claim(session_factory, "instance-b", takeover_at) is True

        assert await renew_backup_slot_lease(
            session_factory=session_factory,
            slot_key=SLOT,
            owner_token="instance-a",
            lease_seconds=LEASE_SECONDS,
            now=takeover_at,
        ) is False
        assert await finish_backup_slot(
            session_factory=session_factory,
            slot_key=SLOT,
            owner_token="instance-a",
            status=BACKUP_SLOT_COMPLETED,
            now=takeover_at,
        ) is False
        assert (await _load(session_factory)).owner_token == "instance-b"
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_renewed_lease_is_not_reclaimed():
    engine, session_factory = await _session_factory()
    try:
        assert await _claim(session_factory, "instance-a", START) is True
        renewed_at = START + timedelta(minutes=10)
        assert await renew_backup_slot_lease(
            session_factory=session_factory,
            slot_key=SLOT,
            owner_token="instance-a",
            lease_seconds=LEASE_SECONDS,
            now=renewed_at,
        ) is True

        # The original lease would have expired at START + 15 minutes; the renewed one runs to +25.
        assert await _claim(session_factory, "instance-b", START + timedelta(minutes=16)) is False
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_completed_and_failed_slots_are_never_claimed_again():
    engine, session_factory = await _session_factory()
    try:
        assert await _claim(session_factory, "instance-a", START) is True
        assert await finish_backup_slot(
            session_factory=session_factory,
            slot_key=SLOT,
            owner_token="instance-a",
            status=BACKUP_SLOT_COMPLETED,
            now=START + timedelta(minutes=2),
        ) is True
        assert await _claim(session_factory, "instance-b", START + timedelta(days=1)) is False
        assert (await _load(session_factory)).status == BACKUP_SLOT_COMPLETED
    finally:
        await engine.dispose()

    engine, session_factory = await _session_factory()
    try:
        assert await _claim(session_factory, "instance-a", START) is True
        assert await finish_backup_slot(
            session_factory=session_factory,
            slot_key=SLOT,
            owner_token="instance-a",
            status=BACKUP_SLOT_FAILED,
            error="pg_dump failed " * 100,
            now=START + timedelta(minutes=2),
        ) is True
        assert await _claim(session_factory, "instance-b", START + timedelta(days=1)) is False
        row = await _load(session_factory)
        assert row.status == BACKUP_SLOT_FAILED
        assert row.last_error is not None
        assert len(row.last_error) <= 500
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_scheduled_backup_runs_once_when_two_instances_fire_for_the_same_slot(monkeypatch: pytest.MonkeyPatch):
    engine, session_factory = await _session_factory()
    sent: list[str] = []

    async def fake_send_daily_backup(*, bot, settings) -> None:
        sent.append("sent")

    monkeypatch.setattr(backup, "send_daily_backup", fake_send_daily_backup)
    bot = SimpleNamespace()
    settings = SimpleNamespace()
    try:
        for _ in range(2):
            await backup.run_scheduled_daily_backup(
                bot=bot,
                settings=settings,
                session_factory=session_factory,
                slot_key=SLOT,
            )

        assert sent == ["sent"]
        assert (await _load(session_factory)).status == BACKUP_SLOT_COMPLETED
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_failed_scheduled_backup_marks_slot_failed_and_still_raises(monkeypatch: pytest.MonkeyPatch):
    engine, session_factory = await _session_factory()

    async def failing_send_daily_backup(*, bot, settings) -> None:
        raise RuntimeError("pg_dump failed for main bot database: boom")

    monkeypatch.setattr(backup, "send_daily_backup", failing_send_daily_backup)
    try:
        with pytest.raises(RuntimeError, match="pg_dump failed"):
            await backup.run_scheduled_daily_backup(
                bot=SimpleNamespace(),
                settings=SimpleNamespace(),
                session_factory=session_factory,
                slot_key=SLOT,
            )

        row = await _load(session_factory)
        assert row.status == BACKUP_SLOT_FAILED
        assert row.last_error is not None and "pg_dump failed" in row.last_error
    finally:
        await engine.dispose()


def test_every_instance_computes_the_same_scheduled_slot_for_one_day():
    early = backup.next_backup_slot(timezone_name="Asia/Barnaul", now=datetime(2026, 10, 7, 10, 0, tzinfo=timezone.utc))
    late = backup.next_backup_slot(timezone_name="Asia/Barnaul", now=datetime(2026, 10, 7, 11, 30, tzinfo=timezone.utc))

    assert early == late
    assert backup._scheduled_slot_key(early) == "daily:2026-10-08"
