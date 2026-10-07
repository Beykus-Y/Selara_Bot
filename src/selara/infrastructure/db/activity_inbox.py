from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from sqlalchemy import Select, delete, func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from selara.infrastructure.db.activity_batching import (
    ActivityBatchMessage,
    activity_batch_message_from_payload,
    activity_batch_message_to_payload,
)
from selara.infrastructure.db.models import ActivityEventDeadLetterModel, ActivityEventInboxModel


@dataclass(frozen=True, slots=True)
class ActivityInboxRow:
    id: int
    chat_id: int
    chat_type: str
    chat_title: str | None
    payload: dict[str, object]
    created_at: datetime
    attempts: int

    def to_event(self) -> ActivityBatchMessage:
        return activity_batch_message_from_payload(
            self.payload,
            chat_id=self.chat_id,
            chat_type=self.chat_type,
            chat_title=self.chat_title,
        )


@dataclass(frozen=True, slots=True)
class ActivityInboxFailure:
    attempts: int
    parked: bool


@dataclass(frozen=True, slots=True)
class ActivityInboxBacklog:
    pending: int
    oldest_created_at: datetime | None
    dead_letters: int


_ROW_COLUMNS = (
    ActivityEventInboxModel.id,
    ActivityEventInboxModel.chat_id,
    ActivityEventInboxModel.chat_type,
    ActivityEventInboxModel.chat_title,
    ActivityEventInboxModel.payload,
    ActivityEventInboxModel.created_at,
    ActivityEventInboxModel.attempts,
)


def stage_activity_inbox_events(session: AsyncSession, events: Sequence[ActivityBatchMessage]) -> None:
    """Add events to the caller's transaction. They are durable only once the caller commits."""
    session.add_all(
        [
            ActivityEventInboxModel(
                chat_id=event.chat_id,
                chat_type=event.chat_type,
                chat_title=event.chat_title,
                payload=activity_batch_message_to_payload(event),
            )
            for event in events
        ]
    )


def _skip_locked_on_postgresql(session: AsyncSession, stmt: Select[Any]) -> Select[Any]:
    bind = session.bind
    if bind is not None and bind.dialect.name == "postgresql":
        return stmt.with_for_update(skip_locked=True)
    return stmt


def _row_from_columns(values: Sequence[Any]) -> ActivityInboxRow:
    row_id, chat_id, chat_type, chat_title, payload, created_at, attempts = values
    return ActivityInboxRow(
        id=int(row_id),
        chat_id=int(chat_id),
        chat_type=chat_type,
        chat_title=chat_title,
        payload=payload,
        created_at=created_at,
        attempts=int(attempts),
    )


async def claim_activity_inbox_batch(session: AsyncSession, *, limit: int) -> list[ActivityInboxRow]:
    """Lock up to `limit` oldest rows. On PostgreSQL, rows locked by another flusher are skipped, not waited on.

    Rows are claimed in id order. A request that commits later can hold a lower id, so rows are not always
    applied in commit order.
    """
    stmt = _skip_locked_on_postgresql(
        session,
        select(*_ROW_COLUMNS).order_by(ActivityEventInboxModel.id).limit(limit),
    )
    rows = (await session.execute(stmt)).all()
    return [_row_from_columns(row) for row in rows]


async def claim_activity_inbox_row(session: AsyncSession, *, row_id: int) -> ActivityInboxRow | None:
    """Lock one row. Returns None if it is gone or another flusher holds it."""
    stmt = _skip_locked_on_postgresql(session, select(*_ROW_COLUMNS).where(ActivityEventInboxModel.id == row_id))
    row = (await session.execute(stmt)).first()
    return None if row is None else _row_from_columns(row)


async def record_activity_inbox_failure(
    session: AsyncSession,
    *,
    row_id: int,
    max_attempts: int,
    failed_at: datetime,
) -> ActivityInboxFailure | None:
    """Count one failed apply of a row. At `max_attempts` the row moves to the dead-letter table, in the same transaction.

    Returns None when the row is gone or another flusher holds it, so there is nothing to count.
    """
    row = await claim_activity_inbox_row(session, row_id=row_id)
    if row is None:
        return None

    attempts = row.attempts + 1
    if attempts < max_attempts:
        await session.execute(
            update(ActivityEventInboxModel).where(ActivityEventInboxModel.id == row_id).values(attempts=attempts)
        )
        return ActivityInboxFailure(attempts=attempts, parked=False)

    session.add(
        ActivityEventDeadLetterModel(
            id=row.id,
            chat_id=row.chat_id,
            chat_type=row.chat_type,
            chat_title=row.chat_title,
            payload=row.payload,
            created_at=row.created_at,
            attempts=attempts,
            failed_at=failed_at,
        )
    )
    await session.execute(delete(ActivityEventInboxModel).where(ActivityEventInboxModel.id == row_id))
    return ActivityInboxFailure(attempts=attempts, parked=True)


async def read_activity_inbox_backlog(session: AsyncSession) -> ActivityInboxBacklog:
    pending, oldest_created_at = (
        await session.execute(
            select(func.count(ActivityEventInboxModel.id), func.min(ActivityEventInboxModel.created_at))
        )
    ).one()
    dead_letters = (await session.execute(select(func.count(ActivityEventDeadLetterModel.id)))).scalar_one()
    return ActivityInboxBacklog(
        pending=int(pending),
        oldest_created_at=oldest_created_at,
        dead_letters=int(dead_letters),
    )


async def retarget_activity_inbox_chat(
    session: AsyncSession,
    *,
    old_chat_id: int,
    new_chat_id: int,
    chat_type: str,
    chat_title: str | None,
) -> None:
    """Move pending and dead-letter rows to a migrated chat id, with the chat's new type and title.

    The flusher writes each row's chat type and title back to the chat row, so rows must not keep the old values.
    A dead letter keeps the old id otherwise, and replaying it would recreate the old chat.
    """
    await session.execute(
        update(ActivityEventInboxModel)
        .where(ActivityEventInboxModel.chat_id == old_chat_id)
        .values(chat_id=new_chat_id, chat_type=chat_type, chat_title=chat_title)
    )
    await session.execute(
        update(ActivityEventDeadLetterModel)
        .where(ActivityEventDeadLetterModel.chat_id == old_chat_id)
        .values(chat_id=new_chat_id, chat_type=chat_type, chat_title=chat_title)
    )


async def delete_activity_inbox_events(session: AsyncSession, row_ids: Sequence[int]) -> None:
    if not row_ids:
        return
    await session.execute(delete(ActivityEventInboxModel).where(ActivityEventInboxModel.id.in_(list(row_ids))))
