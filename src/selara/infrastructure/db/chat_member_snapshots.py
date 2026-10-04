"""Refresh cached Telegram group member counts at a low rate."""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta, timezone

from aiogram import Bot
from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from selara.infrastructure.db.models import ChatMemberCountSnapshotModel, ChatModel

logger = logging.getLogger(__name__)
_GROUP_TYPES = ("group", "supergroup")


async def refresh_chat_member_count_snapshots(
    *, bot: Bot, session_factory: async_sessionmaker[AsyncSession], batch_size: int = 10
) -> int:
    """Refresh up to ``batch_size`` oldest/missing group snapshots."""
    now = datetime.now(timezone.utc)
    cutoff = now - timedelta(hours=24)
    async with session_factory() as session:
        stmt = (
            select(ChatModel.telegram_chat_id)
            .outerjoin(
                ChatMemberCountSnapshotModel,
                ChatMemberCountSnapshotModel.chat_id == ChatModel.telegram_chat_id,
            )
            .where(ChatModel.type.in_(_GROUP_TYPES))
            .where(
                or_(
                    ChatMemberCountSnapshotModel.chat_id.is_(None),
                    ChatMemberCountSnapshotModel.last_checked_at < cutoff,
                )
            )
            .order_by(ChatMemberCountSnapshotModel.last_checked_at.asc().nulls_first())
            .limit(batch_size)
        )
        chat_ids = list((await session.execute(stmt)).scalars())

    refreshed = 0
    for chat_id in chat_ids:
        checked_at = datetime.now(timezone.utc)
        try:
            member_count = await bot.get_chat_member_count(chat_id)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.warning(
                "Telegram member count check failed for chat %s (%s)", chat_id, type(exc).__name__
            )
            async with session_factory() as session:
                snapshot = await session.get(ChatMemberCountSnapshotModel, chat_id)
                if snapshot is None:
                    snapshot = ChatMemberCountSnapshotModel(
                        chat_id=chat_id, member_count=None, last_checked_at=checked_at, last_success_at=None
                    )
                    session.add(snapshot)
                else:
                    snapshot.last_checked_at = checked_at
                await session.commit()
            continue

        async with session_factory() as session:
            snapshot = await session.get(ChatMemberCountSnapshotModel, chat_id)
            if snapshot is None:
                snapshot = ChatMemberCountSnapshotModel(
                    chat_id=chat_id,
                    member_count=int(member_count),
                    last_checked_at=checked_at,
                    last_success_at=checked_at,
                )
                session.add(snapshot)
            else:
                snapshot.member_count = int(member_count)
                snapshot.last_checked_at = checked_at
                snapshot.last_success_at = checked_at
            await session.commit()
            refreshed += 1
    return refreshed


async def run_chat_member_count_snapshot_scheduler(
    *, bot: Bot, session_factory: async_sessionmaker[AsyncSession], interval_seconds: int = 6 * 60 * 60
) -> None:
    while True:
        try:
            await refresh_chat_member_count_snapshots(bot=bot, session_factory=session_factory)
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("Telegram member count snapshot refresh failed")
        await asyncio.sleep(interval_seconds)
