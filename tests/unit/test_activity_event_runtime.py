from datetime import datetime, timedelta, timezone
import importlib.util

import pytest
from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from selara.domain.entities import ChatSnapshot, UserSnapshot
from selara.infrastructure.db.base import Base
from selara.infrastructure.db.models import (
    ChatActivityEventSyncStateModel,
    UserChatActivityModel,
    UserChatMessageEventModel,
)
from selara.infrastructure.db.repositories import SqlAlchemyActivityRepository, SqlAlchemyEconomyRepository

pytestmark = pytest.mark.skipif(importlib.util.find_spec("aiosqlite") is None, reason="aiosqlite is not installed")


@pytest.mark.asyncio
async def test_activity_event_runtime_deduplicates_message_id() -> None:
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    session_factory = async_sessionmaker(engine, expire_on_commit=False)

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    chat = ChatSnapshot(telegram_chat_id=101, chat_type="group", title="Events")
    user = UserSnapshot(telegram_user_id=501, username="alice", first_name="Alice", last_name=None, is_bot=False)
    event_at = datetime(2026, 3, 13, 10, 0, tzinfo=timezone.utc)

    async with session_factory() as session:
        repo = SqlAlchemyActivityRepository(session)
        await repo.upsert_activity(chat=chat, user=user, event_at=event_at, telegram_message_id=77)
        await repo.upsert_activity(
            chat=chat,
            user=user,
            event_at=event_at + timedelta(minutes=1),
            telegram_message_id=77,
        )

        stats = await repo.get_user_stats(chat_id=chat.telegram_chat_id, user_id=user.telegram_user_id)
        event_count = await session.scalar(
            select(func.count(UserChatMessageEventModel.id)).where(UserChatMessageEventModel.chat_id == chat.telegram_chat_id)
        )
        legacy_row = await session.get(
            UserChatActivityModel,
            {"chat_id": chat.telegram_chat_id, "user_id": user.telegram_user_id},
        )

        assert stats is not None
        assert stats.message_count == 1
        assert int(event_count or 0) == 1
        assert legacy_row is not None
        assert int(legacy_row.message_count) == 1

    await engine.dispose()


@pytest.mark.asyncio
async def test_activity_event_runtime_backfill_enables_event_reads() -> None:
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    session_factory = async_sessionmaker(engine, expire_on_commit=False)

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    chat = ChatSnapshot(telegram_chat_id=202, chat_type="group", title="Backfill")
    user_one = UserSnapshot(telegram_user_id=601, username="one", first_name="One", last_name=None, is_bot=False)
    user_two = UserSnapshot(telegram_user_id=602, username="two", first_name="Two", last_name=None, is_bot=False)
    now = datetime.now(timezone.utc).replace(microsecond=0)

    async with session_factory() as session:
        repo = SqlAlchemyActivityRepository(session)
        await repo.upsert_activity(chat=chat, user=user_one, event_at=now - timedelta(hours=6))
        await repo.upsert_activity(chat=chat, user=user_one, event_at=now - timedelta(hours=1))
        await repo.upsert_activity(chat=chat, user=user_two, event_at=now - timedelta(hours=3))
        await repo.set_chat_display_name(chat=chat, user=user_one, display_name="Local One")
        await session.execute(delete(UserChatMessageEventModel).where(UserChatMessageEventModel.chat_id == chat.telegram_chat_id))

        synced = await repo.backfill_message_events_for_chat(chat_id=chat.telegram_chat_id)
        top = await repo.get_top(chat_id=chat.telegram_chat_id, limit=10)
        rep_all, _, rep_last_seen = await repo.get_representation_stats(
            chat_id=chat.telegram_chat_id,
            user_id=user_one.telegram_user_id,
            since=None,
        )
        day_series = await repo.get_chat_activity_daily_series(chat_id=chat.telegram_chat_id, days=1)
        activity_chats = await repo.list_user_activity_chats(user_id=user_one.telegram_user_id, limit=10)
        sync_state = await session.get(ChatActivityEventSyncStateModel, chat.telegram_chat_id)

        assert synced is True
        assert sync_state is not None
        assert sync_state.status == "synced"
        assert top[0].user_id == user_one.telegram_user_id
        assert top[0].message_count == 2
        assert top[0].chat_display_name == "Local One"
        assert rep_all == 2
        assert rep_last_seen == now - timedelta(hours=1)
        assert day_series[-1][1] == 3
        assert activity_chats[0].chat_id == chat.telegram_chat_id
        assert activity_chats[0].message_count == 2

    await engine.dispose()


@pytest.mark.asyncio
async def test_activity_event_runtime_iris_import_creates_events_and_supports_synced_reads() -> None:
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    session_factory = async_sessionmaker(engine, expire_on_commit=False)

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    chat = ChatSnapshot(telegram_chat_id=303, chat_type="group", title="Iris")
    target = UserSnapshot(telegram_user_id=701, username="iris_user", first_name="Iris", last_name=None, is_bot=False)
    imported_at = datetime(2026, 3, 12, 9, 0, tzinfo=timezone.utc)

    async with session_factory() as session:
        repo = SqlAlchemyActivityRepository(session)
        await repo.apply_user_chat_iris_import(
            chat=chat,
            target=target,
            imported_by_user_id=None,
            source_bot_username="iris_moon_bot",
            source_target_username="iris_user",
            imported_at=imported_at,
            profile_text="profile raw",
            awards_text="awards raw",
            karma_base_all_time=14,
            first_seen_at=datetime(2026, 1, 18, 5, 0, tzinfo=timezone.utc),
            last_seen_at=imported_at,
            activity_1d=4,
            activity_7d=11,
            activity_30d=30,
            activity_all=99,
            awards=[],
        )

        event_count = await session.scalar(
            select(func.count(UserChatMessageEventModel.id)).where(
                UserChatMessageEventModel.chat_id == chat.telegram_chat_id,
                UserChatMessageEventModel.user_id == target.telegram_user_id,
            )
        )
        sync_state = await session.get(ChatActivityEventSyncStateModel, chat.telegram_chat_id)

        stats = await repo.get_user_stats(chat_id=chat.telegram_chat_id, user_id=target.telegram_user_id)
        activity_7d, _, last_seen_at = await repo.get_representation_stats(
            chat_id=chat.telegram_chat_id,
            user_id=target.telegram_user_id,
            since=imported_at - timedelta(days=7),
        )
        leaderboard = await repo.get_leaderboard(
            chat_id=chat.telegram_chat_id,
            mode="activity",
            period="7d",
            since=imported_at - timedelta(days=7),
            limit=10,
            karma_weight=0.0,
            activity_weight=1.0,
        )

        assert int(event_count or 0) == 99
        assert sync_state is not None
        assert sync_state.status == "synced"
        assert int(sync_state.legacy_total_messages or 0) == 99
        assert int(sync_state.event_total_messages or 0) == 99
        assert stats is not None
        assert stats.message_count == 99
        assert activity_7d == 11
        assert last_seen_at == imported_at
        assert leaderboard
        assert leaderboard[0].user_id == target.telegram_user_id
        assert leaderboard[0].activity_value == 11

    await engine.dispose()


@pytest.mark.asyncio
async def test_activity_event_runtime_economy_does_not_wipe_known_username() -> None:
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    session_factory = async_sessionmaker(engine, expire_on_commit=False)

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    chat = ChatSnapshot(telegram_chat_id=404, chat_type="group", title="Economy")
    user = UserSnapshot(telegram_user_id=808, username="known_user", first_name="Known", last_name=None, is_bot=False)
    now = datetime(2026, 3, 13, 12, 0, tzinfo=timezone.utc)

    async with session_factory() as session:
        activity_repo = SqlAlchemyActivityRepository(session)
        economy_repo = SqlAlchemyEconomyRepository(session)

        await activity_repo.upsert_activity(chat=chat, user=user, event_at=now)
        await economy_repo._upsert_user(
            UserSnapshot(
                telegram_user_id=user.telegram_user_id,
                username=None,
                first_name=None,
                last_name=None,
                is_bot=False,
            )
        )

        snapshot = await activity_repo.get_user_snapshot(user_id=user.telegram_user_id)
        leaderboard = await activity_repo.get_leaderboard(
            chat_id=chat.telegram_chat_id,
            mode="activity",
            period="all",
            since=None,
            limit=10,
            karma_weight=0.0,
            activity_weight=1.0,
        )

        assert snapshot is not None
        assert snapshot.username == "known_user"
        assert leaderboard
        assert leaderboard[0].username == "known_user"
        assert leaderboard[0].first_name == "Known"

    await engine.dispose()
