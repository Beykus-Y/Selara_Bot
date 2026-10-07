from __future__ import annotations

from collections.abc import Sequence

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from selara.infrastructure.db.activity_batching import (
    ActivityBatchMessage,
    activity_batch_message_from_payload,
    activity_batch_message_to_payload,
)
from selara.infrastructure.db.models import ActivityEventInboxModel


def stage_activity_inbox_events(session: AsyncSession, events: Sequence[ActivityBatchMessage]) -> None:
    """Add events to the caller's transaction. They are durable only once the caller commits."""
    session.add_all(
        [
            ActivityEventInboxModel(chat_id=event.chat_id, payload=activity_batch_message_to_payload(event))
            for event in events
        ]
    )


async def claim_activity_inbox_batch(session: AsyncSession, *, limit: int) -> list[tuple[int, ActivityBatchMessage]]:
    """Lock up to `limit` oldest rows. On PostgreSQL, rows locked by another flusher are skipped, not waited on."""
    stmt = select(ActivityEventInboxModel.id, ActivityEventInboxModel.payload).order_by(ActivityEventInboxModel.id)
    stmt = stmt.limit(limit)
    bind = session.bind
    if bind is not None and bind.dialect.name == "postgresql":
        stmt = stmt.with_for_update(skip_locked=True)
    rows = (await session.execute(stmt)).all()
    return [(int(row_id), activity_batch_message_from_payload(payload)) for row_id, payload in rows]


async def delete_activity_inbox_events(session: AsyncSession, row_ids: Sequence[int]) -> None:
    if not row_ids:
        return
    await session.execute(delete(ActivityEventInboxModel).where(ActivityEventInboxModel.id.in_(list(row_ids))))
