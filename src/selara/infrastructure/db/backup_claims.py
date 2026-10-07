"""Durable claims that let only one bot instance run each scheduled backup slot."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from selara.infrastructure.db.models import BackupJobClaimModel

BACKUP_SLOT_RUNNING = "running"
BACKUP_SLOT_COMPLETED = "completed"
BACKUP_SLOT_FAILED = "failed"

_LAST_ERROR_MAX_CHARS = 500


def _utc_now(now: datetime | None) -> datetime:
    current = now or datetime.now(timezone.utc)
    if current.tzinfo is None:
        return current.replace(tzinfo=timezone.utc)
    return current.astimezone(timezone.utc)


async def try_claim_backup_slot(
    *,
    session_factory: async_sessionmaker[AsyncSession],
    slot_key: str,
    owner_token: str,
    lease_seconds: float,
    now: datetime | None = None,
) -> bool:
    """Return True when this owner may run the slot.

    The first instance inserts the slot row. A later instance may take over only a
    running slot whose lease has expired, which means its owner stopped renewing it.
    Completed and failed slots are never claimed again.
    """
    current = _utc_now(now)
    lease_expires_at = current + timedelta(seconds=lease_seconds)

    async with session_factory() as session:
        session.add(
            BackupJobClaimModel(
                slot_key=slot_key,
                status=BACKUP_SLOT_RUNNING,
                owner_token=owner_token,
                lease_expires_at=lease_expires_at,
                claimed_at=current,
            )
        )
        try:
            await session.commit()
            return True
        except IntegrityError:
            await session.rollback()

    async with session_factory() as session:
        result = await session.execute(
            update(BackupJobClaimModel)
            .where(
                BackupJobClaimModel.slot_key == slot_key,
                BackupJobClaimModel.status == BACKUP_SLOT_RUNNING,
                BackupJobClaimModel.lease_expires_at < current,
            )
            .values(owner_token=owner_token, lease_expires_at=lease_expires_at, claimed_at=current, last_error=None)
            .execution_options(synchronize_session=False)
        )
        await session.commit()
        return result.rowcount == 1


async def read_backup_slot_status(
    *,
    session_factory: async_sessionmaker[AsyncSession],
    slot_key: str,
) -> str | None:
    async with session_factory() as session:
        return await session.scalar(
            select(BackupJobClaimModel.status).where(BackupJobClaimModel.slot_key == slot_key)
        )


async def renew_backup_slot_lease(
    *,
    session_factory: async_sessionmaker[AsyncSession],
    slot_key: str,
    owner_token: str,
    lease_seconds: float,
    now: datetime | None = None,
) -> bool:
    """Extend the lease held by owner_token; False means the lease was taken over or the slot ended."""
    current = _utc_now(now)
    async with session_factory() as session:
        result = await session.execute(
            update(BackupJobClaimModel)
            .where(
                BackupJobClaimModel.slot_key == slot_key,
                BackupJobClaimModel.owner_token == owner_token,
                BackupJobClaimModel.status == BACKUP_SLOT_RUNNING,
            )
            .values(lease_expires_at=current + timedelta(seconds=lease_seconds))
            .execution_options(synchronize_session=False)
        )
        await session.commit()
        return result.rowcount == 1


async def finish_backup_slot(
    *,
    session_factory: async_sessionmaker[AsyncSession],
    slot_key: str,
    owner_token: str,
    status: str,
    error: str | None = None,
    now: datetime | None = None,
) -> bool:
    """Record the terminal status of a slot that owner_token is still running."""
    if status not in (BACKUP_SLOT_COMPLETED, BACKUP_SLOT_FAILED):
        raise ValueError(f"Unsupported terminal backup slot status: {status}")
    current = _utc_now(now)
    async with session_factory() as session:
        result = await session.execute(
            update(BackupJobClaimModel)
            .where(
                BackupJobClaimModel.slot_key == slot_key,
                BackupJobClaimModel.owner_token == owner_token,
                BackupJobClaimModel.status == BACKUP_SLOT_RUNNING,
            )
            .values(
                status=status,
                finished_at=current,
                last_error=error[:_LAST_ERROR_MAX_CHARS] if error else None,
            )
            .execution_options(synchronize_session=False)
        )
        await session.commit()
        return result.rowcount == 1
