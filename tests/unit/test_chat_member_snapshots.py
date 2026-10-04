from __future__ import annotations

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from aiogram.exceptions import TelegramForbiddenError
from aiogram.methods import GetChatMemberCount

from selara.infrastructure.db.base import Base
from selara.infrastructure.db.chat_member_snapshots import (
    _snapshot_batch_size,
    refresh_chat_member_count_snapshots,
)
from selara.infrastructure.db.models import ChatMemberCountSnapshotModel, ChatModel


def test_snapshot_batch_size_matches_scheduler_interval_and_group_count():
    assert _snapshot_batch_size(10, 15 * 60) == 1
    assert _snapshot_batch_size(1000, 15 * 60) == 11
    assert _snapshot_batch_size(100_000, 15 * 60) == 50


@pytest.mark.asyncio
async def test_failed_member_count_check_retries_after_scheduler_interval():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    bot = SimpleNamespace(get_chat_member_count=AsyncMock(side_effect=[RuntimeError("Telegram unavailable"), 55]))
    try:
        async with session_factory() as session:
            session.add(ChatModel(telegram_chat_id=-1001, type="supergroup", title="Group"))
            await session.commit()

        assert await refresh_chat_member_count_snapshots(
            bot=bot, session_factory=session_factory, interval_seconds=15 * 60
        ) == 0
        async with session_factory() as session:
            failed = await session.get(ChatMemberCountSnapshotModel, -1001)
            assert failed is not None
            assert failed.member_count is None
            assert failed.last_success_at is None
            assert failed.last_error_at is not None

        assert await refresh_chat_member_count_snapshots(
            bot=bot, session_factory=session_factory, interval_seconds=15 * 60
        ) == 0
        assert bot.get_chat_member_count.await_count == 1

        async with session_factory() as session:
            failed = await session.get(ChatMemberCountSnapshotModel, -1001)
            assert failed is not None
            failed.last_error_at = datetime.now(timezone.utc) - timedelta(minutes=16)
            await session.commit()

        assert await refresh_chat_member_count_snapshots(
            bot=bot, session_factory=session_factory, interval_seconds=15 * 60
        ) == 1
        async with session_factory() as session:
            succeeded = await session.get(ChatMemberCountSnapshotModel, -1001)
            assert succeeded is not None
            assert succeeded.member_count == 55
            assert succeeded.last_success_at is not None
            assert succeeded.last_error_at is None
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_forbidden_group_is_retired_until_bot_membership_returns():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    denied = TelegramForbiddenError(
        method=GetChatMemberCount(chat_id=-1002),
        message="Forbidden: bot is not a member of the chat",
    )
    bot = SimpleNamespace(get_chat_member_count=AsyncMock(side_effect=denied))
    try:
        async with session_factory() as session:
            session.add(ChatModel(telegram_chat_id=-1002, type="supergroup", title="Removed group"))
            await session.commit()

        assert await refresh_chat_member_count_snapshots(bot=bot, session_factory=session_factory) == 0
        async with session_factory() as session:
            removed_group = await session.get(ChatModel, -1002)
            assert removed_group is not None
            assert removed_group.is_bot_member is False
        assert await refresh_chat_member_count_snapshots(bot=bot, session_factory=session_factory) == 0
        assert bot.get_chat_member_count.await_count == 1
    finally:
        await engine.dispose()
