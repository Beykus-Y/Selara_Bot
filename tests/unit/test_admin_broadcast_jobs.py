from __future__ import annotations

import importlib.util
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from selara.infrastructure.db import admin_broadcast_jobs as jobs
from selara.infrastructure.db.base import Base
from selara.infrastructure.db.models import AdminBroadcastDeliveryModel, AdminBroadcastModel
from selara.infrastructure.db.repositories import SqlAlchemyActivityRepository

pytestmark = pytest.mark.skipif(importlib.util.find_spec("aiosqlite") is None, reason="aiosqlite is not installed")

T0 = datetime(2026, 10, 7, 12, 0, tzinfo=timezone.utc)


@asynccontextmanager
async def _session_factory():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    try:
        yield async_sessionmaker(engine, expire_on_commit=False)
    finally:
        await engine.dispose()


async def _seed(session_factory, *, statuses: list[str], media: bytes | None = None) -> int:
    async with session_factory() as session:
        broadcast = AdminBroadcastModel(
            body="<b>Проверка</b>",
            rendered_body="<b>Проверка</b>",
            active_since_days=3,
            media_type="photo" if media is not None else None,
            media_content=media,
            media_filename="photo.jpg" if media is not None else None,
            created_by_user_id=77,
        )
        session.add(broadcast)
        await session.flush()
        broadcast_id = int(broadcast.id)
        for index, status in enumerate(statuses):
            session.add(
                AdminBroadcastDeliveryModel(
                    broadcast_id=broadcast_id,
                    chat_id=-1000 - index,
                    chat_title_snapshot=f"Чат {index}",
                    status=status,
                    reaction_mode="none",
                )
            )
        await session.commit()
    return broadcast_id


async def _delivery_rows(session_factory, broadcast_id: int) -> list:
    async with session_factory() as session:
        result = await session.execute(
            select(AdminBroadcastDeliveryModel.status, AdminBroadcastDeliveryModel.error_text)
            .where(AdminBroadcastDeliveryModel.broadcast_id == broadcast_id)
            .order_by(AdminBroadcastDeliveryModel.id)
        )
        return list(result.all())


async def _acquire(session_factory, broadcast_id: int, owner: str, at: datetime) -> bool:
    return await jobs.try_acquire_admin_broadcast_lease(
        session_factory=session_factory, broadcast_id=broadcast_id, owner_token=owner, lease_seconds=60, now=at
    )


async def _claim(session_factory, broadcast_id: int, owner: str, at: datetime):
    return await jobs.claim_next_admin_broadcast_delivery(
        session_factory=session_factory, broadcast_id=broadcast_id, owner_token=owner, claim_seconds=60, now=at
    )


@pytest.mark.asyncio
async def test_lease_is_exclusive_until_it_expires_and_only_its_owner_can_renew_or_release() -> None:
    async with _session_factory() as session_factory:
        broadcast_id = await _seed(session_factory, statuses=["pending"])

        assert await _acquire(session_factory, broadcast_id, "owner-a", T0)
        assert not await _acquire(session_factory, broadcast_id, "owner-b", T0 + timedelta(seconds=10))
        assert not await jobs.renew_admin_broadcast_lease(
            session_factory=session_factory, broadcast_id=broadcast_id, owner_token="owner-b", lease_seconds=60,
            now=T0 + timedelta(seconds=20),
        )
        assert await jobs.renew_admin_broadcast_lease(
            session_factory=session_factory, broadcast_id=broadcast_id, owner_token="owner-a", lease_seconds=60,
            now=T0 + timedelta(seconds=20),
        )
        # The renewal moved the expiry to T0 + 80s, so owner-b is still blocked at T0 + 70s.
        assert not await _acquire(session_factory, broadcast_id, "owner-b", T0 + timedelta(seconds=70))
        assert await _acquire(session_factory, broadcast_id, "owner-b", T0 + timedelta(seconds=100))
        assert not await jobs.release_admin_broadcast_lease(
            session_factory=session_factory, broadcast_id=broadcast_id, owner_token="owner-a"
        )
        assert await jobs.release_admin_broadcast_lease(
            session_factory=session_factory, broadcast_id=broadcast_id, owner_token="owner-b"
        )


@pytest.mark.asyncio
async def test_each_pending_delivery_is_claimed_once_and_expired_claims_are_never_sent_again() -> None:
    async with _session_factory() as session_factory:
        broadcast_id = await _seed(session_factory, statuses=["pending", "pending", "sent"])

        first = await _claim(session_factory, broadcast_id, "owner-a", T0)
        second = await _claim(session_factory, broadcast_id, "owner-b", T0)
        assert first is not None and second is not None
        assert first.id != second.id
        assert await _claim(session_factory, broadcast_id, "owner-c", T0) is None

        # Both claims expire without an outcome, as if the worker died mid-send. Those deliveries are recorded as
        # failed and nothing is handed out again.
        assert await _claim(session_factory, broadcast_id, "owner-c", T0 + timedelta(seconds=120)) is None
        rows = await _delivery_rows(session_factory, broadcast_id)

    assert [row.status for row in rows] == ["failed", "failed", "sent"]
    assert rows[0].error_text == jobs.ADMIN_BROADCAST_INTERRUPTED


@pytest.mark.asyncio
async def test_cancel_stops_unclaimed_deliveries_and_leaves_the_claimed_one_to_its_worker() -> None:
    async with _session_factory() as session_factory:
        broadcast_id = await _seed(session_factory, statuses=["pending", "pending", "sent"])
        claimed = await _claim(session_factory, broadcast_id, "owner-a", T0)
        assert claimed is not None

        stopped = await jobs.cancel_admin_broadcast(session_factory=session_factory, broadcast_id=broadcast_id, now=T0)

        assert stopped == 1
        assert not await _acquire(session_factory, broadcast_id, "owner-b", T0)
        rows = await _delivery_rows(session_factory, broadcast_id)

    assert [row.status for row in rows] == ["pending", "failed", "sent"]
    assert rows[1].error_text == jobs.ADMIN_BROADCAST_CANCELLED


@pytest.mark.asyncio
async def test_stored_photo_is_kept_until_nothing_is_left_to_send() -> None:
    async with _session_factory() as session_factory:
        broadcast_id = await _seed(session_factory, statuses=["pending"], media=b"photo-bytes")
        job = await jobs.load_admin_broadcast_send_job(session_factory=session_factory, broadcast_id=broadcast_id)
        assert job is not None
        assert job.media_content == b"photo-bytes" and job.media_filename == "photo.jpg"

        assert await _acquire(session_factory, broadcast_id, "owner-a", T0)
        claimed = await _claim(session_factory, broadcast_id, "owner-a", T0)
        assert claimed is not None
        # The delivery is still pending while its send is in flight, so the photo has to stay.
        assert await jobs.release_admin_broadcast_lease(
            session_factory=session_factory, broadcast_id=broadcast_id, owner_token="owner-a"
        )
        job = await jobs.load_admin_broadcast_send_job(session_factory=session_factory, broadcast_id=broadcast_id)
        assert job is not None and job.media_content == b"photo-bytes"

        async with session_factory() as session:
            await SqlAlchemyActivityRepository(session).mark_admin_broadcast_delivery_sent(
                delivery_id=claimed.id, telegram_message_id=1, sent_at=T0
            )
            await session.commit()
        assert await _acquire(session_factory, broadcast_id, "owner-b", T0)
        assert await jobs.release_admin_broadcast_lease(
            session_factory=session_factory, broadcast_id=broadcast_id, owner_token="owner-b"
        )
        job = await jobs.load_admin_broadcast_send_job(session_factory=session_factory, broadcast_id=broadcast_id)

    assert job is not None and job.media_content is None and job.media_filename is None
