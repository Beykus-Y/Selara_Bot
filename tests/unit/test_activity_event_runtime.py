from datetime import datetime, timedelta, timezone
import importlib.util

import pytest
from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from selara.domain.entities import ChatSnapshot, UserSnapshot
from selara.infrastructure.db.activity_batching import ActivityBatchMessage
from selara.infrastructure.db.base import Base
from selara.infrastructure.db.models import (
    ChatActivityEventSyncStateModel,
    ChatMetricsModel,
    MessageArchiveModel,
    UserChatActivityDailyModel,
    UserChatActivityMinuteModel,
    UserChatActivityModel,
    UserChatMessageEventModel,
)
from selara.infrastructure.db.repositories import SqlAlchemyActivityRepository, SqlAlchemyEconomyRepository

pytestmark = pytest.mark.skipif(importlib.util.find_spec("aiosqlite") is None, reason="aiosqlite is not installed")


def _utc_or_naive_as_utc(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


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
async def test_activity_event_runtime_flush_batch_archives_created_message_and_counts_activity() -> None:
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    session_factory = async_sessionmaker(engine, expire_on_commit=False)

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    event_at = datetime(2026, 3, 13, 10, 0, tzinfo=timezone.utc)

    async with session_factory() as session:
        repo = SqlAlchemyActivityRepository(session)
        await repo.flush_activity_batch(
            [
                ActivityBatchMessage(
                    chat_id=1505,
                    chat_type="group",
                    chat_title="Archive",
                    user_id=1909,
                    username="alice",
                    first_name="Alice",
                    last_name=None,
                    is_bot=False,
                    event_at=event_at,
                    telegram_message_id=77,
                    count_as_activity=True,
                    snapshot_kind="created",
                    snapshot_at=event_at,
                    sent_at=event_at,
                    edited_at=None,
                    message_type="text",
                    text="hello",
                    caption=None,
                    raw_message_json={"message_id": 77, "text": "hello"},
                    snapshot_hash="created-hash",
                )
            ]
        )

        archive_rows = (
            await session.execute(select(MessageArchiveModel).where(MessageArchiveModel.chat_id == 1505))
        ).scalars().all()
        activity_row = await session.get(UserChatActivityModel, {"chat_id": 1505, "user_id": 1909})

        assert len(archive_rows) == 1
        assert archive_rows[0].snapshot_kind == "created"
        assert archive_rows[0].message_type == "text"
        assert archive_rows[0].text == "hello"
        assert archive_rows[0].edited_at is None
        assert activity_row is not None
        assert int(activity_row.message_count) == 1

    await engine.dispose()


@pytest.mark.asyncio
async def test_activity_event_runtime_flush_batch_archives_edited_message_without_touching_activity() -> None:
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    session_factory = async_sessionmaker(engine, expire_on_commit=False)

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    event_at = datetime(2026, 3, 13, 10, 0, tzinfo=timezone.utc)
    edited_at = event_at + timedelta(minutes=5)

    async with session_factory() as session:
        repo = SqlAlchemyActivityRepository(session)
        await repo.flush_activity_batch(
            [
                ActivityBatchMessage(
                    chat_id=1606,
                    chat_type="group",
                    chat_title="Archive",
                    user_id=1909,
                    username="alice",
                    first_name="Alice",
                    last_name=None,
                    is_bot=False,
                    event_at=event_at,
                    telegram_message_id=77,
                    count_as_activity=True,
                    snapshot_kind="created",
                    snapshot_at=event_at,
                    sent_at=event_at,
                    edited_at=None,
                    message_type="text",
                    text="hello",
                    caption=None,
                    raw_message_json={"message_id": 77, "text": "hello"},
                    snapshot_hash="created-hash",
                )
            ]
        )
        await repo.flush_activity_batch(
            [
                ActivityBatchMessage(
                    chat_id=1606,
                    chat_type="group",
                    chat_title="Archive",
                    user_id=1909,
                    username="alice",
                    first_name="Alice",
                    last_name=None,
                    is_bot=False,
                    event_at=event_at,
                    telegram_message_id=77,
                    count_as_activity=False,
                    snapshot_kind="edited",
                    snapshot_at=edited_at,
                    sent_at=event_at,
                    edited_at=edited_at,
                    message_type="text",
                    text="hello there",
                    caption=None,
                    raw_message_json={"message_id": 77, "text": "hello there", "edit_date": "2026-03-13T10:05:00Z"},
                    snapshot_hash="edited-hash",
                )
            ]
        )

        archive_rows = (
            await session.execute(
                select(MessageArchiveModel)
                .where(MessageArchiveModel.chat_id == 1606)
                .order_by(MessageArchiveModel.snapshot_at.asc())
            )
        ).scalars().all()
        activity_row = await session.get(UserChatActivityModel, {"chat_id": 1606, "user_id": 1909})

        assert len(archive_rows) == 2
        assert [row.snapshot_kind for row in archive_rows] == ["created", "edited"]
        assert _utc_or_naive_as_utc(archive_rows[-1].edited_at) == edited_at
        assert archive_rows[-1].text == "hello there"
        assert activity_row is not None
        assert int(activity_row.message_count) == 1

    await engine.dispose()


@pytest.mark.asyncio
async def test_activity_event_runtime_flush_batch_accepts_unix_timestamps_for_edited_archive_fields() -> None:
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    session_factory = async_sessionmaker(engine, expire_on_commit=False)

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    event_at = datetime(2026, 3, 13, 10, 0, tzinfo=timezone.utc)
    edited_at = event_at + timedelta(minutes=5)
    edited_ts = int(edited_at.timestamp())

    async with session_factory() as session:
        repo = SqlAlchemyActivityRepository(session)
        await repo.flush_activity_batch(
            [
                ActivityBatchMessage(
                    chat_id=1656,
                    chat_type="group",
                    chat_title="Archive",
                    user_id=1909,
                    username="alice",
                    first_name="Alice",
                    last_name=None,
                    is_bot=False,
                    event_at=event_at,
                    telegram_message_id=77,
                    count_as_activity=False,
                    snapshot_kind="edited",
                    snapshot_at=edited_ts,
                    sent_at=event_at,
                    edited_at=edited_ts,
                    message_type="text",
                    text="hello there",
                    caption=None,
                    raw_message_json={"message_id": 77, "text": "hello there", "edit_date": edited_ts},
                    snapshot_hash="edited-unix-hash",
                )
            ]
        )

        archive_rows = (
            await session.execute(
                select(MessageArchiveModel)
                .where(MessageArchiveModel.chat_id == 1656)
                .order_by(MessageArchiveModel.snapshot_at.asc())
            )
        ).scalars().all()
        activity_row = await session.get(UserChatActivityModel, {"chat_id": 1656, "user_id": 1909})

        assert len(archive_rows) == 1
        assert archive_rows[0].snapshot_kind == "edited"
        assert _utc_or_naive_as_utc(archive_rows[0].snapshot_at) == edited_at
        assert _utc_or_naive_as_utc(archive_rows[0].edited_at) == edited_at
        assert archive_rows[0].text == "hello there"
        assert activity_row is None

    await engine.dispose()


@pytest.mark.asyncio
async def test_activity_event_runtime_flush_batch_deduplicates_duplicate_archive_snapshot() -> None:
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    session_factory = async_sessionmaker(engine, expire_on_commit=False)

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    event_at = datetime(2026, 3, 13, 10, 0, tzinfo=timezone.utc)

    async with session_factory() as session:
        repo = SqlAlchemyActivityRepository(session)
        batch_event = ActivityBatchMessage(
            chat_id=1707,
            chat_type="group",
            chat_title="Archive",
            user_id=1909,
            username="alice",
            first_name="Alice",
            last_name=None,
            is_bot=False,
            event_at=event_at,
            telegram_message_id=77,
            count_as_activity=True,
            snapshot_kind="created",
            snapshot_at=event_at,
            sent_at=event_at,
            edited_at=None,
            message_type="text",
            text="hello",
            caption=None,
            raw_message_json={"message_id": 77, "text": "hello"},
            snapshot_hash="duplicate-hash",
        )

        await repo.flush_activity_batch([batch_event])
        await repo.flush_activity_batch([batch_event])

        archive_count = await session.scalar(select(func.count(MessageArchiveModel.id)).where(MessageArchiveModel.chat_id == 1707))
        event_count = await session.scalar(
            select(func.count(UserChatMessageEventModel.id)).where(UserChatMessageEventModel.chat_id == 1707)
        )
        activity_row = await session.get(UserChatActivityModel, {"chat_id": 1707, "user_id": 1909})

        assert int(archive_count or 0) == 1
        assert int(event_count or 0) == 1
        assert activity_row is not None
        assert int(activity_row.message_count) == 1

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


@pytest.mark.asyncio
async def test_period_leaderboard_includes_active_zero_message_members_and_filters_inactive() -> None:
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    session_factory = async_sessionmaker(engine, expire_on_commit=False)

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    chat = ChatSnapshot(telegram_chat_id=405, chat_type="group", title="Leaderboard")
    speaker = UserSnapshot(telegram_user_id=901, username="speaker", first_name="Speaker", last_name=None, is_bot=False)
    quiet = UserSnapshot(telegram_user_id=902, username="quiet", first_name="Quiet", last_name=None, is_bot=False)
    left = UserSnapshot(telegram_user_id=903, username="left", first_name="Left", last_name=None, is_bot=False)
    now = datetime(2026, 3, 13, 12, 0, tzinfo=timezone.utc)

    async with session_factory() as session:
        repo = SqlAlchemyActivityRepository(session)

        await repo.upsert_activity(chat=chat, user=speaker, event_at=now)
        await repo.upsert_activity(chat=chat, user=speaker, event_at=now + timedelta(minutes=1))
        await repo.set_chat_member_active(chat=chat, user=quiet, is_active=True, event_at=now)
        await repo.set_chat_member_active(chat=chat, user=left, is_active=False, event_at=now)

        leaderboard = await repo.get_leaderboard(
            chat_id=chat.telegram_chat_id,
            mode="activity",
            period="day",
            since=now - timedelta(days=1),
            limit=10,
            karma_weight=0.0,
            activity_weight=1.0,
        )
        under_one = await repo.get_leaderboard(
            chat_id=chat.telegram_chat_id,
            mode="activity",
            period="day",
            since=now - timedelta(days=1),
            limit=10,
            karma_weight=0.0,
            activity_weight=1.0,
            activity_less_than=1,
        )

        values_by_id = {item.user_id: item.activity_value for item in leaderboard}
        assert values_by_id == {speaker.telegram_user_id: 2, quiet.telegram_user_id: 0}
        assert [item.user_id for item in under_one] == [quiet.telegram_user_id]

    await engine.dispose()


@pytest.mark.asyncio
async def test_activity_event_runtime_flush_batch_deduplicates_duplicate_message_ids() -> None:
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    session_factory = async_sessionmaker(engine, expire_on_commit=False)

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    event_at = datetime(2026, 3, 13, 10, 0, tzinfo=timezone.utc)

    async with session_factory() as session:
        repo = SqlAlchemyActivityRepository(session)
        result = await repo.flush_activity_batch(
            [
                ActivityBatchMessage(
                    chat_id=505,
                    chat_type="group",
                    chat_title="Batch",
                    user_id=909,
                    username="alice",
                    first_name="Alice",
                    last_name=None,
                    is_bot=False,
                    event_at=event_at,
                    telegram_message_id=77,
                ),
                ActivityBatchMessage(
                    chat_id=505,
                    chat_type="group",
                    chat_title="Batch",
                    user_id=909,
                    username="alice_new",
                    first_name="Alice",
                    last_name=None,
                    is_bot=False,
                    event_at=event_at + timedelta(minutes=1),
                    telegram_message_id=77,
                ),
            ]
        )

        event_count = await session.scalar(
            select(func.count(UserChatMessageEventModel.id)).where(UserChatMessageEventModel.chat_id == 505)
        )
        activity_row = await session.get(UserChatActivityModel, {"chat_id": 505, "user_id": 909})

        assert int(event_count or 0) == 1
        assert activity_row is not None
        assert int(activity_row.message_count) == 1
        assert result.impacted_chat_ids == {505}
        assert result.latest_event_at_by_pair == {(505, 909): event_at}

    await engine.dispose()


@pytest.mark.asyncio
async def test_activity_event_runtime_flush_batch_updates_aggregates_across_users_and_chats() -> None:
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    session_factory = async_sessionmaker(engine, expire_on_commit=False)

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    start = datetime(2026, 3, 13, 12, 0, tzinfo=timezone.utc)

    async with session_factory() as session:
        repo = SqlAlchemyActivityRepository(session)
        result = await repo.flush_activity_batch(
            [
                ActivityBatchMessage(
                    chat_id=606,
                    chat_type="group",
                    chat_title="One",
                    user_id=701,
                    username="one",
                    first_name="One",
                    last_name=None,
                    is_bot=False,
                    event_at=start,
                    telegram_message_id=1,
                ),
                ActivityBatchMessage(
                    chat_id=606,
                    chat_type="group",
                    chat_title="One",
                    user_id=701,
                    username="one",
                    first_name="One",
                    last_name=None,
                    is_bot=False,
                    event_at=start + timedelta(minutes=1),
                    telegram_message_id=2,
                ),
                ActivityBatchMessage(
                    chat_id=606,
                    chat_type="group",
                    chat_title="One",
                    user_id=702,
                    username="two",
                    first_name="Two",
                    last_name=None,
                    is_bot=False,
                    event_at=start + timedelta(minutes=1),
                    telegram_message_id=3,
                ),
                ActivityBatchMessage(
                    chat_id=607,
                    chat_type="group",
                    chat_title="Two",
                    user_id=703,
                    username="three",
                    first_name="Three",
                    last_name=None,
                    is_bot=False,
                    event_at=start + timedelta(minutes=2),
                    telegram_message_id=4,
                ),
            ]
        )

        activity_one = await session.get(UserChatActivityModel, {"chat_id": 606, "user_id": 701})
        activity_two = await session.get(UserChatActivityModel, {"chat_id": 606, "user_id": 702})
        activity_three = await session.get(UserChatActivityModel, {"chat_id": 607, "user_id": 703})
        daily_rows = (
            await session.execute(
                select(UserChatActivityDailyModel).where(UserChatActivityDailyModel.chat_id.in_([606, 607]))
            )
        ).scalars().all()
        minute_rows = (
            await session.execute(
                select(UserChatActivityMinuteModel).where(UserChatActivityMinuteModel.chat_id.in_([606, 607]))
            )
        ).scalars().all()

        assert activity_one is not None
        assert activity_two is not None
        assert activity_three is not None
        assert int(activity_one.message_count) == 2
        assert int(activity_two.message_count) == 1
        assert int(activity_three.message_count) == 1
        assert len(daily_rows) == 3
        assert len(minute_rows) == 4
        assert result.impacted_chat_ids == {606, 607}
        assert result.latest_event_at_by_pair[(606, 701)] == start + timedelta(minutes=1)

    await engine.dispose()


@pytest.mark.asyncio
async def test_activity_event_runtime_flush_batch_reactivates_inactive_member_and_updates_metrics() -> None:
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    session_factory = async_sessionmaker(engine, expire_on_commit=False)

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    chat = ChatSnapshot(telegram_chat_id=808, chat_type="group", title="Reactivate")
    user = UserSnapshot(telegram_user_id=909, username="react", first_name="React", last_name=None, is_bot=False)
    now = datetime(2026, 3, 13, 15, 0, tzinfo=timezone.utc)

    async with session_factory() as session:
        repo = SqlAlchemyActivityRepository(session)
        await repo.upsert_activity(chat=chat, user=user, event_at=now - timedelta(days=1), telegram_message_id=1)
        await repo.set_chat_member_active(chat=chat, user=user, is_active=False, event_at=now - timedelta(hours=1))

        metrics = await session.get(ChatMetricsModel, chat.telegram_chat_id)
        assert metrics is not None
        assert int(metrics.active_members_count) == 0

        result = await repo.flush_activity_batch(
            [
                ActivityBatchMessage(
                    chat_id=chat.telegram_chat_id,
                    chat_type=chat.chat_type,
                    chat_title=chat.title,
                    user_id=user.telegram_user_id,
                    username=user.username,
                    first_name=user.first_name,
                    last_name=user.last_name,
                    is_bot=user.is_bot,
                    event_at=now,
                    telegram_message_id=2,
                )
            ]
        )

        refreshed_metrics = await session.get(ChatMetricsModel, chat.telegram_chat_id)
        refreshed_activity = await session.get(
            UserChatActivityModel,
            {"chat_id": chat.telegram_chat_id, "user_id": user.telegram_user_id},
        )

        assert refreshed_metrics is not None
        assert refreshed_activity is not None
        assert int(refreshed_metrics.active_members_count) == 1
        assert refreshed_activity.is_active_member is True
        assert int(refreshed_activity.message_count) == 2
        assert result.latest_event_at_by_pair[(chat.telegram_chat_id, user.telegram_user_id)] == now

    await engine.dispose()


@pytest.mark.asyncio
async def test_activity_event_runtime_tops_exclude_inactive_members_without_deleting_history() -> None:
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    session_factory = async_sessionmaker(engine, expire_on_commit=False)

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    chat = ChatSnapshot(telegram_chat_id=9090, chat_type="group", title="Top Filter")
    active_user = UserSnapshot(telegram_user_id=1001, username="active", first_name="Active", last_name=None, is_bot=False)
    inactive_user = UserSnapshot(telegram_user_id=1002, username="inactive", first_name="Inactive", last_name=None, is_bot=False)
    voter_user = UserSnapshot(telegram_user_id=1003, username="voter", first_name="Voter", last_name=None, is_bot=False)
    now = datetime(2026, 3, 13, 18, 0, tzinfo=timezone.utc)

    async with session_factory() as session:
        repo = SqlAlchemyActivityRepository(session)
        await repo.upsert_activity(chat=chat, user=active_user, event_at=now - timedelta(hours=2), telegram_message_id=1)
        await repo.upsert_activity(chat=chat, user=inactive_user, event_at=now - timedelta(hours=3), telegram_message_id=2)
        await repo.upsert_activity(chat=chat, user=voter_user, event_at=now - timedelta(hours=4), telegram_message_id=3)
        await repo.record_vote(chat=chat, voter=voter_user, target=active_user, vote_value=1, event_at=now - timedelta(minutes=5))
        await repo.record_vote(chat=chat, voter=active_user, target=inactive_user, vote_value=1, event_at=now - timedelta(minutes=4))
        await repo.set_chat_member_active(chat=chat, user=inactive_user, is_active=False, event_at=now - timedelta(minutes=1))

        legacy_top = await repo.get_top(chat_id=chat.telegram_chat_id, limit=10)
        legacy_karma = await repo.get_leaderboard(
            chat_id=chat.telegram_chat_id,
            mode="karma",
            period="all",
            since=None,
            limit=10,
            karma_weight=1.0,
            activity_weight=0.0,
        )

        assert inactive_user.telegram_user_id not in {item.user_id for item in legacy_top}
        assert inactive_user.telegram_user_id not in {item.user_id for item in legacy_karma}

        synced = await repo.backfill_message_events_for_chat(chat_id=chat.telegram_chat_id)
        synced_top = await repo.get_top(chat_id=chat.telegram_chat_id, limit=10)
        synced_karma = await repo.get_leaderboard(
            chat_id=chat.telegram_chat_id,
            mode="karma",
            period="all",
            since=None,
            limit=10,
            karma_weight=1.0,
            activity_weight=0.0,
        )
        inactive_row = await session.get(
            UserChatActivityModel,
            {"chat_id": chat.telegram_chat_id, "user_id": inactive_user.telegram_user_id},
        )

        assert synced is True
        assert inactive_row is not None
        assert int(inactive_row.message_count) == 1
        assert inactive_row.is_active_member is False
        assert inactive_user.telegram_user_id not in {item.user_id for item in synced_top}
        assert inactive_user.telegram_user_id not in {item.user_id for item in synced_karma}

    await engine.dispose()


@pytest.mark.asyncio
async def test_activity_event_runtime_excludes_rest_users_from_tops_and_inactive_lists() -> None:
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    session_factory = async_sessionmaker(engine, expire_on_commit=False)

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    chat = ChatSnapshot(telegram_chat_id=9191, chat_type="group", title="Rest Filter")
    actor = UserSnapshot(telegram_user_id=2000, username="actor", first_name="Actor", last_name=None, is_bot=False)
    active_user = UserSnapshot(telegram_user_id=2001, username="active", first_name="Active", last_name=None, is_bot=False)
    rested_user = UserSnapshot(telegram_user_id=2002, username="rested", first_name="Rested", last_name=None, is_bot=False)
    inactive_user = UserSnapshot(telegram_user_id=2003, username="silent", first_name="Silent", last_name=None, is_bot=False)
    now = datetime(2026, 3, 20, 18, 0, tzinfo=timezone.utc)

    async with session_factory() as session:
        repo = SqlAlchemyActivityRepository(session)
        await repo.upsert_activity(chat=chat, user=active_user, event_at=now - timedelta(hours=1), telegram_message_id=1)
        await repo.upsert_activity(chat=chat, user=rested_user, event_at=now - timedelta(days=3), telegram_message_id=2)
        await repo.upsert_activity(chat=chat, user=rested_user, event_at=now - timedelta(days=2, hours=23), telegram_message_id=3)
        await repo.upsert_activity(chat=chat, user=rested_user, event_at=now - timedelta(days=2, hours=22), telegram_message_id=4)
        await repo.upsert_activity(chat=chat, user=inactive_user, event_at=now - timedelta(days=2), telegram_message_id=5)
        await repo.record_vote(chat=chat, voter=active_user, target=rested_user, vote_value=1, event_at=now - timedelta(hours=2))
        await repo.record_vote(chat=chat, voter=active_user, target=inactive_user, vote_value=1, event_at=now - timedelta(hours=3))

        first_rest = await repo.grant_rest(chat=chat, actor=actor, target=rested_user, duration_days=2)
        second_rest = await repo.grant_rest(chat=chat, actor=actor, target=rested_user, duration_days=1)
        current_rest = await repo.get_active_rest_state(chat_id=chat.telegram_chat_id, user_id=rested_user.telegram_user_id)
        active_rest_entries = await repo.list_active_rest_entries(chat_id=chat.telegram_chat_id)

        legacy_top = await repo.get_top(chat_id=chat.telegram_chat_id, limit=10)
        legacy_karma = await repo.get_leaderboard(
            chat_id=chat.telegram_chat_id,
            mode="karma",
            period="all",
            since=None,
            limit=10,
            karma_weight=1.0,
            activity_weight=0.0,
        )
        legacy_inactive = await repo.list_inactive_members(
            chat_id=chat.telegram_chat_id,
            inactive_since=now - timedelta(days=1),
        )

        synced = await repo.backfill_message_events_for_chat(chat_id=chat.telegram_chat_id)
        synced_top = await repo.get_top(chat_id=chat.telegram_chat_id, limit=10)
        synced_karma = await repo.get_leaderboard(
            chat_id=chat.telegram_chat_id,
            mode="karma",
            period="all",
            since=None,
            limit=10,
            karma_weight=1.0,
            activity_weight=0.0,
        )
        synced_inactive = await repo.list_inactive_members(
            chat_id=chat.telegram_chat_id,
            inactive_since=now - timedelta(days=1),
        )

        revoked = await repo.revoke_rest(chat=chat, actor=actor, target=rested_user)
        after_revoke = await repo.get_active_rest_state(chat_id=chat.telegram_chat_id, user_id=rested_user.telegram_user_id)

        assert current_rest is not None
        assert second_rest.expires_at > first_rest.expires_at
        assert current_rest.expires_at == second_rest.expires_at
        assert [entry.user.telegram_user_id for entry in active_rest_entries] == [rested_user.telegram_user_id]
        assert rested_user.telegram_user_id not in {item.user_id for item in legacy_top}
        assert rested_user.telegram_user_id not in {item.user_id for item in legacy_karma}
        assert rested_user.telegram_user_id not in {item.user_id for item in legacy_inactive}
        assert inactive_user.telegram_user_id in {item.user_id for item in legacy_inactive}

        assert synced is True
        assert rested_user.telegram_user_id not in {item.user_id for item in synced_top}
        assert rested_user.telegram_user_id not in {item.user_id for item in synced_karma}
        assert rested_user.telegram_user_id not in {item.user_id for item in synced_inactive}
        assert inactive_user.telegram_user_id in {item.user_id for item in synced_inactive}

        assert revoked is not None
        assert after_revoke is None

    await engine.dispose()
