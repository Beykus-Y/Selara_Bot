"""Refresh cached Telegram group member counts at a low rate."""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta, timezone

from aiogram import Bot
from aiogram.exceptions import TelegramForbiddenError
from sqlalchemy import and_, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from selara.infrastructure.db.models import ChatMemberCountSnapshotModel, ChatModel

logger = logging.getLogger(__name__)
_GROUP_TYPES = ("group", "supergroup")


def _snapshot_batch_size(group_count: int, interval_seconds: int) -> int:
    intervals_per_freshness_window = max(1, (24 * 60 * 60) // max(interval_seconds, 1))
    needed = (max(group_count, 0) + intervals_per_freshness_window - 1) // intervals_per_freshness_window
    return min(50, max(1, needed))


async def refresh_chat_member_count_snapshots(
    *,
    bot: Bot,
    session_factory: async_sessionmaker[AsyncSession],
    batch_size: int | None = None,
    interval_seconds: int = 15 * 60,
) -> int:
    """Refresh up to ``batch_size`` oldest/missing group snapshots."""
    now = datetime.now(timezone.utc)
    stale_success_before = now - timedelta(hours=24)
    retry_failure_before = now - timedelta(seconds=max(60, interval_seconds))
    async with session_factory() as session:
        if batch_size is None:
            group_count = int(
                await session.scalar(
                    select(func.count())
                    .select_from(ChatModel)
                    .where(ChatModel.type.in_(_GROUP_TYPES), ChatModel.is_bot_member.is_(True))
                )
                or 0
            )
            batch_size = _snapshot_batch_size(group_count, interval_seconds)
        stmt = (
            select(ChatModel.telegram_chat_id)
            .outerjoin(
                ChatMemberCountSnapshotModel,
                ChatMemberCountSnapshotModel.chat_id == ChatModel.telegram_chat_id,
            )
            .where(ChatModel.type.in_(_GROUP_TYPES))
            .where(ChatModel.is_bot_member.is_(True))
            .where(
                or_(
                    ChatMemberCountSnapshotModel.chat_id.is_(None),
                    and_(
                        ChatMemberCountSnapshotModel.last_error_at.is_not(None),
                        ChatMemberCountSnapshotModel.last_error_at < retry_failure_before,
                    ),
                    and_(
                        ChatMemberCountSnapshotModel.last_error_at.is_(None),
                        or_(
                            ChatMemberCountSnapshotModel.last_success_at.is_(None),
                            ChatMemberCountSnapshotModel.last_success_at < stale_success_before,
                        ),
                    ),
                )
            )
            .order_by(
                func.coalesce(
                    ChatMemberCountSnapshotModel.last_error_at,
                    ChatMemberCountSnapshotModel.last_success_at,
                    ChatMemberCountSnapshotModel.last_checked_at,
                ).asc()
            )
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
                if isinstance(exc, TelegramForbiddenError):
                    chat = await session.get(ChatModel, chat_id)
                    if chat is not None:
                        chat.is_bot_member = False
                snapshot = await session.get(ChatMemberCountSnapshotModel, chat_id)
                if snapshot is None:
                    snapshot = ChatMemberCountSnapshotModel(
                        chat_id=chat_id,
                        member_count=None,
                        last_checked_at=checked_at,
                        last_success_at=None,
                        last_error_at=checked_at,
                    )
                    session.add(snapshot)
                else:
                    snapshot.last_checked_at = checked_at
                    snapshot.last_error_at = checked_at
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
                    last_error_at=None,
                )
                session.add(snapshot)
            else:
                snapshot.member_count = int(member_count)
                snapshot.last_checked_at = checked_at
                snapshot.last_success_at = checked_at
                snapshot.last_error_at = None
            await session.commit()
            refreshed += 1
    return refreshed


async def run_chat_member_count_snapshot_scheduler(
    *, bot: Bot, session_factory: async_sessionmaker[AsyncSession], interval_seconds: int = 15 * 60
) -> None:
    while True:
        try:
            await refresh_chat_member_count_snapshots(
                bot=bot,
                session_factory=session_factory,
                interval_seconds=interval_seconds,
            )
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("Telegram member count snapshot refresh failed")
        await asyncio.sleep(interval_seconds)
