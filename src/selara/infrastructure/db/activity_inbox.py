from __future__ import annotations

from collections.abc import Sequence

from sqlalchemy import delete, select, update
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
            ActivityEventInboxModel(
                chat_id=event.chat_id,
                chat_type=event.chat_type,
                chat_title=event.chat_title,
                payload=activity_batch_message_to_payload(event),
            )
            for event in events
        ]
    )


async def claim_activity_inbox_batch(session: AsyncSession, *, limit: int) -> list[tuple[int, ActivityBatchMessage]]:
    """Lock up to `limit` oldest rows. On PostgreSQL, rows locked by another flusher are skipped, not waited on."""
    stmt = select(
        ActivityEventInboxModel.id,
        ActivityEventInboxModel.chat_id,
        ActivityEventInboxModel.chat_type,
        ActivityEventInboxModel.chat_title,
        ActivityEventInboxModel.payload,
    ).order_by(ActivityEventInboxModel.id)
    stmt = stmt.limit(limit)
    bind = session.bind
    if bind is not None and bind.dialect.name == "postgresql":
        stmt = stmt.with_for_update(skip_locked=True)
    rows = (await session.execute(stmt)).all()
    return [
        (
            int(row_id),
            activity_batch_message_from_payload(
                payload,
                chat_id=chat_id,
                chat_type=chat_type,
                chat_title=chat_title,
            ),
        )
        for row_id, chat_id, chat_type, chat_title, payload in rows
    ]


async def retarget_activity_inbox_chat(
    session: AsyncSession,
    *,
    old_chat_id: int,
    new_chat_id: int,
    chat_type: str,
    chat_title: str | None,
) -> None:
    """Move pending rows to a migrated chat id, with the chat's new type and title.

    The flusher writes each row's chat type and title back to the chat row, so rows must not keep the old values.
    """
    await session.execute(
        update(ActivityEventInboxModel)
        .where(ActivityEventInboxModel.chat_id == old_chat_id)
        .values(chat_id=new_chat_id, chat_type=chat_type, chat_title=chat_title)
    )


async def delete_activity_inbox_events(session: AsyncSession, row_ids: Sequence[int]) -> None:
    if not row_ids:
        return
    await session.execute(delete(ActivityEventInboxModel).where(ActivityEventInboxModel.id.in_(list(row_ids))))
