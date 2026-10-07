"""Durable leases and delivery claims that let an admin broadcast be resumed without sending a chat twice.

One worker at a time holds a broadcast's lease, renewing it while it sends. Each pending delivery is claimed before
its Telegram call and resolved afterwards. A claim that expires without an outcome means the send may or may not have
reached the chat, so that delivery is recorded as failed instead of being sent again.
"""

from __future__ import annotations

import secrets
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from sqlalchemy import func, or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from selara.infrastructure.db.models import AdminBroadcastDeliveryModel, AdminBroadcastModel

ADMIN_BROADCAST_LEASE_SECONDS = 240
ADMIN_BROADCAST_CLAIM_SECONDS = 240

ADMIN_BROADCAST_CANCELLED = "cancelled_by_admin"
ADMIN_BROADCAST_INTERRUPTED = "interrupted_during_send"
ADMIN_BROADCAST_PHOTO_UNAVAILABLE = "photo_unavailable_after_restart"

_MAX_CLAIM_ATTEMPTS = 20


@dataclass(frozen=True, slots=True)
class AdminBroadcastSendJob:
    id: int
    body: str
    media_type: str | None
    media_file_id: str | None
    media_filename: str | None
    media_content: bytes | None


@dataclass(frozen=True, slots=True)
class ClaimedAdminBroadcastDelivery:
    id: int
    chat_id: int


def new_admin_broadcast_owner_token() -> str:
    return secrets.token_hex(16)


def _utc_now(now: datetime | None) -> datetime:
    current = now or datetime.now(timezone.utc)
    if current.tzinfo is None:
        return current.replace(tzinfo=timezone.utc)
    return current.astimezone(timezone.utc)


async def try_acquire_admin_broadcast_lease(
    *,
    session_factory: async_sessionmaker[AsyncSession],
    broadcast_id: int,
    owner_token: str,
    lease_seconds: float = ADMIN_BROADCAST_LEASE_SECONDS,
    now: datetime | None = None,
) -> bool:
    """Return True when owner_token may work this broadcast. A cancelled broadcast or a live lease blocks it."""
    current = _utc_now(now)
    async with session_factory() as session:
        result = await session.execute(
            update(AdminBroadcastModel)
            .where(
                AdminBroadcastModel.id == int(broadcast_id),
                AdminBroadcastModel.cancelled_at.is_(None),
                or_(AdminBroadcastModel.lease_expires_at.is_(None), AdminBroadcastModel.lease_expires_at < current),
            )
            .values(lease_owner_token=owner_token, lease_expires_at=current + timedelta(seconds=lease_seconds))
            .execution_options(synchronize_session=False)
        )
        await session.commit()
        return result.rowcount == 1


async def renew_admin_broadcast_lease(
    *,
    session_factory: async_sessionmaker[AsyncSession],
    broadcast_id: int,
    owner_token: str,
    lease_seconds: float = ADMIN_BROADCAST_LEASE_SECONDS,
    now: datetime | None = None,
) -> bool:
    """Extend the lease held by owner_token. False means another owner took it over, so the worker must stop."""
    current = _utc_now(now)
    async with session_factory() as session:
        result = await session.execute(
            update(AdminBroadcastModel)
            .where(AdminBroadcastModel.id == int(broadcast_id), AdminBroadcastModel.lease_owner_token == owner_token)
            .values(lease_expires_at=current + timedelta(seconds=lease_seconds))
            .execution_options(synchronize_session=False)
        )
        await session.commit()
        return result.rowcount == 1


async def release_admin_broadcast_lease(
    *,
    session_factory: async_sessionmaker[AsyncSession],
    broadcast_id: int,
    owner_token: str,
) -> bool:
    """Drop the lease held by owner_token. The stored photo is only needed while something is still pending."""
    async with session_factory() as session:
        pending = await session.scalar(
            select(func.count())
            .select_from(AdminBroadcastDeliveryModel)
            .where(
                AdminBroadcastDeliveryModel.broadcast_id == int(broadcast_id),
                AdminBroadcastDeliveryModel.status == "pending",
            )
        )
        values: dict[str, object | None] = {"lease_owner_token": None, "lease_expires_at": None}
        if not pending:
            values.update(media_content=None, media_filename=None)
        result = await session.execute(
            update(AdminBroadcastModel)
            .where(AdminBroadcastModel.id == int(broadcast_id), AdminBroadcastModel.lease_owner_token == owner_token)
            .values(**values)
            .execution_options(synchronize_session=False)
        )
        await session.commit()
        return result.rowcount == 1


async def load_admin_broadcast_send_job(
    *,
    session_factory: async_sessionmaker[AsyncSession],
    broadcast_id: int,
) -> AdminBroadcastSendJob | None:
    async with session_factory() as session:
        row = (
            await session.execute(
                select(
                    AdminBroadcastModel.id,
                    AdminBroadcastModel.body,
                    AdminBroadcastModel.media_type,
                    AdminBroadcastModel.media_file_id,
                    AdminBroadcastModel.media_filename,
                    AdminBroadcastModel.media_content,
                ).where(AdminBroadcastModel.id == int(broadcast_id))
            )
        ).one_or_none()
    if row is None:
        return None
    return AdminBroadcastSendJob(
        id=int(row.id),
        body=row.body,
        media_type=row.media_type,
        media_file_id=row.media_file_id,
        media_filename=row.media_filename,
        media_content=bytes(row.media_content) if row.media_content is not None else None,
    )


async def claim_next_admin_broadcast_delivery(
    *,
    session_factory: async_sessionmaker[AsyncSession],
    broadcast_id: int,
    owner_token: str,
    claim_seconds: float = ADMIN_BROADCAST_CLAIM_SECONDS,
    now: datetime | None = None,
) -> ClaimedAdminBroadcastDelivery | None:
    """Claim the lowest-id pending delivery nobody holds. Claims that expired without an outcome are failed first."""
    current = _utc_now(now)
    for _ in range(_MAX_CLAIM_ATTEMPTS):
        async with session_factory() as session:
            await session.execute(
                update(AdminBroadcastDeliveryModel)
                .where(
                    AdminBroadcastDeliveryModel.broadcast_id == int(broadcast_id),
                    AdminBroadcastDeliveryModel.status == "pending",
                    AdminBroadcastDeliveryModel.claim_expires_at < current,
                )
                .values(
                    status="failed",
                    error_text=ADMIN_BROADCAST_INTERRUPTED,
                    claim_token=None,
                    claim_expires_at=None,
                )
                .execution_options(synchronize_session=False)
            )
            candidate = (
                await session.execute(
                    select(AdminBroadcastDeliveryModel.id, AdminBroadcastDeliveryModel.chat_id)
                    .where(
                        AdminBroadcastDeliveryModel.broadcast_id == int(broadcast_id),
                        AdminBroadcastDeliveryModel.status == "pending",
                        AdminBroadcastDeliveryModel.claim_token.is_(None),
                    )
                    .order_by(AdminBroadcastDeliveryModel.id)
                    .limit(1)
                )
            ).one_or_none()
            if candidate is None:
                await session.commit()
                return None
            result = await session.execute(
                update(AdminBroadcastDeliveryModel)
                .where(
                    AdminBroadcastDeliveryModel.id == int(candidate.id),
                    AdminBroadcastDeliveryModel.status == "pending",
                    AdminBroadcastDeliveryModel.claim_token.is_(None),
                )
                .values(claim_token=owner_token, claim_expires_at=current + timedelta(seconds=claim_seconds))
                .execution_options(synchronize_session=False)
            )
            await session.commit()
            if result.rowcount == 1:
                return ClaimedAdminBroadcastDelivery(id=int(candidate.id), chat_id=int(candidate.chat_id))
    return None


async def _fail_unclaimed_deliveries(
    session: AsyncSession,
    *,
    broadcast_id: int,
    error_text: str,
    current: datetime,
) -> int:
    result = await session.execute(
        update(AdminBroadcastDeliveryModel)
        .where(
            AdminBroadcastDeliveryModel.broadcast_id == int(broadcast_id),
            AdminBroadcastDeliveryModel.status == "pending",
            or_(
                AdminBroadcastDeliveryModel.claim_token.is_(None),
                AdminBroadcastDeliveryModel.claim_expires_at < current,
            ),
        )
        .values(status="failed", error_text=error_text, claim_token=None, claim_expires_at=None)
        .execution_options(synchronize_session=False)
    )
    return int(result.rowcount or 0)


async def fail_unclaimed_admin_broadcast_deliveries(
    *,
    session_factory: async_sessionmaker[AsyncSession],
    broadcast_id: int,
    error_text: str,
    now: datetime | None = None,
) -> int:
    current = _utc_now(now)
    async with session_factory() as session:
        stopped = await _fail_unclaimed_deliveries(
            session, broadcast_id=broadcast_id, error_text=error_text, current=current
        )
        await session.commit()
    return stopped


async def cancel_admin_broadcast(
    *,
    session_factory: async_sessionmaker[AsyncSession],
    broadcast_id: int,
    now: datetime | None = None,
) -> int:
    """Stop the deliveries nobody has started. A delivery already claimed is resolved by its own worker.

    Returns how many deliveries were stopped. When that is zero nothing was cancelled and the broadcast is left alone.
    """
    current = _utc_now(now)
    async with session_factory() as session:
        stopped = await _fail_unclaimed_deliveries(
            session, broadcast_id=broadcast_id, error_text=ADMIN_BROADCAST_CANCELLED, current=current
        )
        if stopped:
            await session.execute(
                update(AdminBroadcastModel)
                .where(AdminBroadcastModel.id == int(broadcast_id), AdminBroadcastModel.cancelled_at.is_(None))
                .values(cancelled_at=current)
                .execution_options(synchronize_session=False)
            )
        await session.commit()
    return stopped


async def admin_broadcast_is_leased(
    session: AsyncSession,
    *,
    broadcast_id: int,
    now: datetime | None = None,
) -> bool:
    current = _utc_now(now)
    live = await session.scalar(
        select(func.count())
        .select_from(AdminBroadcastModel)
        .where(
            AdminBroadcastModel.id == int(broadcast_id),
            AdminBroadcastModel.lease_owner_token.is_not(None),
            AdminBroadcastModel.lease_expires_at > current,
        )
    )
    return bool(live)
