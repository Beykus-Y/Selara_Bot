"""Retention for archived group messages and parked activity events (issue #79).

The archive keeps raw Telegram JSON, text, captions and voice transcripts. Once a row's snapshot is older than
MESSAGE_ARCHIVE_RETENTION_DAYS, the row is deleted, which removes that content with it. Parked activity dead
letters carry the same payloads, so they follow the same window. Deletes run in batches, each in its own
transaction, so no statement holds locks on a table for long. Aggregates live in other tables and are untouched.
The cleanup is off unless MESSAGE_ARCHIVE_RETENTION_DAYS is set, because the bot cannot restore deleted rows.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from sqlalchemy.orm import InstrumentedAttribute

from selara.infrastructure.db.models import ActivityEventDeadLetterModel, MessageArchiveModel

logger = logging.getLogger(__name__)

DELETE_BATCH_SIZE = 500
# Pause between batches, so a large first cleanup does not compete with the bot's own writes.
BATCH_PAUSE_SECONDS = 0.5
# The first run waits a little, so start-up work and the first activity flushes go first.
FIRST_RUN_DELAY_SECONDS = 60
RUN_INTERVAL_SECONDS = 60 * 60


@dataclass(frozen=True, slots=True)
class MessageArchivePurgeResult:
    archive_rows: int = 0
    dead_letters: int = 0


async def purge_expired_message_archive(
    session_factory: async_sessionmaker[AsyncSession],
    *,
    retention_days: int,
    now: datetime | None = None,
    batch_size: int = DELETE_BATCH_SIZE,
    pause_seconds: float = 0.0,
) -> MessageArchivePurgeResult:
    """Delete archive rows and dead letters older than retention_days. A value of 0 turns the purge off."""
    if retention_days <= 0:
        return MessageArchivePurgeResult()
    cutoff = (now or datetime.now(timezone.utc)) - timedelta(days=retention_days)
    archive_rows = await _delete_before_cutoff(
        session_factory,
        model=MessageArchiveModel,
        time_column=MessageArchiveModel.snapshot_at,
        cutoff=cutoff,
        batch_size=batch_size,
        pause_seconds=pause_seconds,
    )
    dead_letters = await _delete_before_cutoff(
        session_factory,
        model=ActivityEventDeadLetterModel,
        time_column=ActivityEventDeadLetterModel.created_at,
        cutoff=cutoff,
        batch_size=batch_size,
        pause_seconds=pause_seconds,
    )
    return MessageArchivePurgeResult(archive_rows=archive_rows, dead_letters=dead_letters)


async def _delete_before_cutoff(
    session_factory: async_sessionmaker[AsyncSession],
    *,
    model: type[MessageArchiveModel] | type[ActivityEventDeadLetterModel],
    time_column: InstrumentedAttribute[datetime],
    cutoff: datetime,
    batch_size: int,
    pause_seconds: float,
) -> int:
    deleted_total = 0
    while True:
        # LIMIT without ORDER BY lets the index on the time column stop after one batch.
        async with session_factory() as session:
            expired_ids = list(
                (await session.execute(select(model.id).where(time_column < cutoff).limit(batch_size))).scalars()
            )
            if expired_ids:
                await session.execute(delete(model).where(model.id.in_(expired_ids)))
            await session.commit()
        deleted_total += len(expired_ids)
        if len(expired_ids) < batch_size:
            return deleted_total
        await asyncio.sleep(pause_seconds)


async def run_message_archive_retention_scheduler(
    *,
    session_factory: async_sessionmaker[AsyncSession],
    retention_days: int,
    interval_seconds: int = RUN_INTERVAL_SECONDS,
) -> None:
    if retention_days <= 0:
        logger.info("Message archive retention is off because MESSAGE_ARCHIVE_RETENTION_DAYS is 0")
        return
    await asyncio.sleep(FIRST_RUN_DELAY_SECONDS)
    while True:
        try:
            result = await purge_expired_message_archive(
                session_factory,
                retention_days=retention_days,
                pause_seconds=BATCH_PAUSE_SECONDS,
            )
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("Message archive retention run failed", extra={"retention_days": retention_days})
        else:
            if result.archive_rows or result.dead_letters:
                logger.info(
                    "Message archive retention deleted expired rows",
                    extra={
                        "retention_days": retention_days,
                        "archive_rows_deleted": result.archive_rows,
                        "dead_letters_deleted": result.dead_letters,
                    },
                )
        await asyncio.sleep(interval_seconds)
