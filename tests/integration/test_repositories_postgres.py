from datetime import datetime, timedelta, timezone
import os

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from selara.domain.entities import ChatSnapshot, UserSnapshot
from selara.infrastructure.db.base import Base
from selara.infrastructure.db.models import MarriageModel, UserChatActivityDailyModel, UserChatIrisImportHistoryModel
from selara.infrastructure.db.repositories import SqlAlchemyActivityRepository


@pytest.mark.integration
@pytest.mark.asyncio
async def test_repository_upsert_and_top_ordering() -> None:
    database_url = os.getenv("TEST_DATABASE_URL")
    if not database_url:
        pytest.skip("TEST_DATABASE_URL is not set")

    engine = create_async_engine(database_url)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
        await conn.run_sync(Base.metadata.create_all)

    chat = ChatSnapshot(telegram_chat_id=1, chat_type="group", title="Group")
    user_1 = UserSnapshot(telegram_user_id=11, username="one", first_name="One", last_name=None, is_bot=False)
    user_2 = UserSnapshot(telegram_user_id=22, username="two", first_name="Two", last_name=None, is_bot=False)

    async with session_factory() as session:
        repo = SqlAlchemyActivityRepository(session)

        await repo.upsert_activity(chat=chat, user=user_1, event_at=datetime(2026, 2, 12, 10, 0, tzinfo=timezone.utc))
        await repo.upsert_activity(chat=chat, user=user_1, event_at=datetime(2026, 2, 12, 10, 5, tzinfo=timezone.utc))
        await repo.upsert_activity(chat=chat, user=user_2, event_at=datetime(2026, 2, 12, 10, 6, tzinfo=timezone.utc))

        top = await repo.get_top(chat_id=1, limit=10)
        assert top[0].user_id == 11
        assert top[0].message_count == 2
        assert top[1].user_id == 22

        await session.commit()

    await engine.dispose()


@pytest.mark.integration
@pytest.mark.asyncio
async def test_repository_chat_aliases_and_modes() -> None:
    database_url = os.getenv("TEST_DATABASE_URL")
    if not database_url:
        pytest.skip("TEST_DATABASE_URL is not set")

    engine = create_async_engine(database_url)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
        await conn.run_sync(Base.metadata.create_all)

    chat = ChatSnapshot(telegram_chat_id=100, chat_type="group", title="Alias Group")

    async with session_factory() as session:
        repo = SqlAlchemyActivityRepository(session)

        assert await repo.get_chat_alias_mode(chat_id=chat.telegram_chat_id) == "both"
        assert await repo.set_chat_alias_mode(chat=chat, mode="aliases_if_exists") == "aliases_if_exists"
        assert await repo.get_chat_alias_mode(chat_id=chat.telegram_chat_id) == "aliases_if_exists"

        created = await repo.upsert_chat_alias(
            chat=chat,
            command_key="naming",
            source_trigger_norm="нейминг",
            alias_text_norm="+ник",
            actor_user_id=555,
            force=False,
        )
        assert created.alias is not None
        assert created.created is True
        assert created.reassigned is False
        assert created.conflict_alias is None

        conflict = await repo.upsert_chat_alias(
            chat=chat,
            command_key="me",
            source_trigger_norm="кто я",
            alias_text_norm="+ник",
            actor_user_id=555,
            force=False,
        )
        assert conflict.alias is None
        assert conflict.conflict_alias is not None
        assert conflict.conflict_alias.command_key == "naming"

        reassigned = await repo.upsert_chat_alias(
            chat=chat,
            command_key="me",
            source_trigger_norm="кто я",
            alias_text_norm="+ник",
            actor_user_id=555,
            force=True,
        )
        assert reassigned.alias is not None
        assert reassigned.alias.command_key == "me"
        assert reassigned.created is False
        assert reassigned.reassigned is True

        rows = await repo.list_chat_aliases(chat_id=chat.telegram_chat_id)
        assert len(rows) == 1
        assert rows[0].alias_text_norm == "+ник"
        assert rows[0].command_key == "me"

        assert await repo.remove_chat_alias(chat_id=chat.telegram_chat_id, alias_text_norm="+ник") is True
        assert await repo.remove_chat_alias(chat_id=chat.telegram_chat_id, alias_text_norm="+ник") is False

        await session.commit()

    await engine.dispose()


@pytest.mark.integration
@pytest.mark.asyncio
async def test_repository_lists_user_admin_and_activity_chats() -> None:
    database_url = os.getenv("TEST_DATABASE_URL")
    if not database_url:
        pytest.skip("TEST_DATABASE_URL is not set")

    engine = create_async_engine(database_url)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
        await conn.run_sync(Base.metadata.create_all)

    user = UserSnapshot(telegram_user_id=501, username="u501", first_name="U", last_name="501", is_bot=False)
    group_owner = ChatSnapshot(telegram_chat_id=-10001, chat_type="group", title="Owner Group")
    group_helper = ChatSnapshot(telegram_chat_id=-10002, chat_type="supergroup", title="Helper Group")
    private_chat = ChatSnapshot(telegram_chat_id=90001, chat_type="private", title="Private")

    async with session_factory() as session:
        repo = SqlAlchemyActivityRepository(session)

        await repo.upsert_activity(chat=group_owner, user=user, event_at=datetime(2026, 2, 12, 9, 0, tzinfo=timezone.utc))
        await repo.upsert_activity(chat=group_helper, user=user, event_at=datetime(2026, 2, 12, 11, 0, tzinfo=timezone.utc))
        await repo.upsert_activity(chat=private_chat, user=user, event_at=datetime(2026, 2, 12, 12, 0, tzinfo=timezone.utc))

        await repo.set_bot_role(chat=group_owner, target=user, role="owner", assigned_by_user_id=user.telegram_user_id)
        await repo.set_bot_role(chat=group_helper, target=user, role="helper", assigned_by_user_id=user.telegram_user_id)

        admin_chats = await repo.list_user_admin_chats(user_id=user.telegram_user_id)
        assert len(admin_chats) == 1
        assert admin_chats[0].chat_id == group_owner.telegram_chat_id
        assert admin_chats[0].bot_role == "owner"

        activity_chats = await repo.list_user_activity_chats(user_id=user.telegram_user_id, limit=10)
        assert len(activity_chats) == 2
        assert activity_chats[0].chat_id == group_helper.telegram_chat_id
        assert activity_chats[0].bot_role == "helper"
        assert activity_chats[1].chat_id == group_owner.telegram_chat_id

        await session.commit()

    await engine.dispose()


@pytest.mark.integration
@pytest.mark.asyncio
async def test_repository_lists_user_manageable_game_chats() -> None:
    database_url = os.getenv("TEST_DATABASE_URL")
    if not database_url:
        pytest.skip("TEST_DATABASE_URL is not set")

    engine = create_async_engine(database_url)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
        await conn.run_sync(Base.metadata.create_all)

    user = UserSnapshot(telegram_user_id=777, username="u777", first_name="Game", last_name="Host", is_bot=False)
    game_chat = ChatSnapshot(telegram_chat_id=-11001, chat_type="group", title="Game Chat")
    settings_chat = ChatSnapshot(telegram_chat_id=-11002, chat_type="group", title="Settings Chat")

    async with session_factory() as session:
        repo = SqlAlchemyActivityRepository(session)

        await repo.upsert_activity(chat=game_chat, user=user, event_at=datetime(2026, 2, 12, 13, 0, tzinfo=timezone.utc))
        await repo.upsert_activity(chat=settings_chat, user=user, event_at=datetime(2026, 2, 12, 14, 0, tzinfo=timezone.utc))

        await repo.set_bot_role(chat=game_chat, target=user, role="senior_admin", assigned_by_user_id=user.telegram_user_id)
        await repo.set_bot_role(chat=settings_chat, target=user, role="helper", assigned_by_user_id=user.telegram_user_id)

        manageable_game_chats = await repo.list_user_manageable_game_chats(user_id=user.telegram_user_id)
        assert len(manageable_game_chats) == 1
        assert manageable_game_chats[0].chat_id == game_chat.telegram_chat_id
        assert manageable_game_chats[0].bot_role == "senior_admin"

        await session.commit()

    await engine.dispose()


@pytest.mark.integration
@pytest.mark.asyncio
async def test_repository_lists_inactive_members_oldest_first_and_skips_bots_and_inactive_members() -> None:
    database_url = os.getenv("TEST_DATABASE_URL")
    if not database_url:
        pytest.skip("TEST_DATABASE_URL is not set")

    engine = create_async_engine(database_url)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
        await conn.run_sync(Base.metadata.create_all)

    chat = ChatSnapshot(telegram_chat_id=-10077, chat_type="group", title="Inactive Group")
    oldest = UserSnapshot(telegram_user_id=1001, username="oldest", first_name="Old", last_name="One", is_bot=False)
    recent = UserSnapshot(telegram_user_id=1002, username="recent", first_name="Recent", last_name="One", is_bot=False)
    left_user = UserSnapshot(telegram_user_id=1003, username="left", first_name="Left", last_name="User", is_bot=False)
    bot_user = UserSnapshot(telegram_user_id=1004, username="service_bot", first_name="Service", last_name="Bot", is_bot=True)
    active_user = UserSnapshot(telegram_user_id=1005, username="active", first_name="Active", last_name="User", is_bot=False)
    now = datetime(2026, 3, 12, 12, 0, tzinfo=timezone.utc)

    async with session_factory() as session:
        repo = SqlAlchemyActivityRepository(session)

        await repo.upsert_activity(chat=chat, user=oldest, event_at=now - timedelta(days=5))
        await repo.set_chat_display_name(chat=chat, user=oldest, display_name="Старый ник")
        await repo.upsert_activity(chat=chat, user=recent, event_at=now - timedelta(days=2, hours=3))
        await repo.upsert_activity(chat=chat, user=left_user, event_at=now - timedelta(days=4))
        await repo.set_chat_member_active(chat=chat, user=left_user, is_active=False, event_at=now - timedelta(days=1))
        await repo.upsert_activity(chat=chat, user=bot_user, event_at=now - timedelta(days=3))
        await repo.upsert_activity(chat=chat, user=active_user, event_at=now - timedelta(hours=10))

        rows = await repo.list_inactive_members(chat_id=chat.telegram_chat_id, inactive_since=now - timedelta(days=1))

        assert [row.user_id for row in rows] == [oldest.telegram_user_id, recent.telegram_user_id]
        assert rows[0].chat_display_name == "Старый ник"

        await session.commit()

    await engine.dispose()


@pytest.mark.integration
@pytest.mark.asyncio
async def test_repository_pair_to_marriage_transition_and_action_usage() -> None:
    database_url = os.getenv("TEST_DATABASE_URL")
    if not database_url:
        pytest.skip("TEST_DATABASE_URL is not set")

    engine = create_async_engine(database_url)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
        await conn.run_sync(Base.metadata.create_all)

    chat = ChatSnapshot(telegram_chat_id=-100500, chat_type="group", title="Relationships")
    user_a = UserSnapshot(telegram_user_id=1001, username="user_a", first_name="A", last_name=None, is_bot=False)
    user_b = UserSnapshot(telegram_user_id=1002, username="user_b", first_name="B", last_name=None, is_bot=False)

    async with session_factory() as session:
        repo = SqlAlchemyActivityRepository(session)
        now = datetime(2026, 2, 16, 10, 0, tzinfo=timezone.utc)

        pair_proposal, error = await repo.create_marriage_proposal(
            chat=chat,
            proposer=user_a,
            target=user_b,
            kind="pair",
            expires_at=now + timedelta(hours=1),
            event_at=now,
        )
        assert pair_proposal is not None
        assert error is None
        assert pair_proposal.kind == "pair"

        _, pair_state, error = await repo.respond_relationship_proposal(
            proposal_id=pair_proposal.id,
            actor_user_id=user_b.telegram_user_id,
            accept=True,
            event_at=now,
        )
        assert error is None
        assert pair_state is not None
        assert pair_state.kind == "pair"

        updated_pair = await repo.touch_pair_affection(
            pair_id=pair_state.id,
            actor_user_id=user_a.telegram_user_id,
            affection_delta=11,
            event_at=now,
        )
        assert updated_pair is not None
        assert updated_pair.affection_points == 11

        marriage_proposal, error = await repo.create_marriage_proposal(
            chat=chat,
            proposer=user_a,
            target=user_b,
            kind="marriage",
            expires_at=now + timedelta(hours=1),
            event_at=now + timedelta(minutes=5),
        )
        assert marriage_proposal is not None
        assert error is None
        assert marriage_proposal.kind == "marriage"

        _, marriage_state, error = await repo.respond_relationship_proposal(
            proposal_id=marriage_proposal.id,
            actor_user_id=user_b.telegram_user_id,
            accept=True,
            event_at=now + timedelta(minutes=5),
        )
        assert error is None
        assert marriage_state is not None
        assert marriage_state.kind == "marriage"
        assert marriage_state.affection_points == 11

        assert await repo.get_active_pair(user_id=user_a.telegram_user_id, chat_id=chat.telegram_chat_id) is None
        marriage = await repo.get_active_marriage(user_id=user_a.telegram_user_id, chat_id=chat.telegram_chat_id)
        assert marriage is not None
        assert marriage.affection_points == 11

        used_at = now + timedelta(minutes=6)
        set_at = await repo.set_relationship_action_last_used_at(
            relationship=marriage_state,
            actor_user_id=user_a.telegram_user_id,
            action_code="care",
            used_at=used_at,
        )
        assert set_at == used_at
        assert (
            await repo.get_relationship_action_last_used_at(
                relationship=marriage_state,
                actor_user_id=user_a.telegram_user_id,
                action_code="care",
            )
            == used_at
        )

        await session.commit()

    await engine.dispose()


@pytest.mark.integration
@pytest.mark.asyncio
async def test_repository_lists_active_marriages_oldest_first() -> None:
    database_url = os.getenv("TEST_DATABASE_URL")
    if not database_url:
        pytest.skip("TEST_DATABASE_URL is not set")

    engine = create_async_engine(database_url)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
        await conn.run_sync(Base.metadata.create_all)

    chat = ChatSnapshot(telegram_chat_id=-100510, chat_type="group", title="Marriage List")
    user_a = UserSnapshot(telegram_user_id=2001, username="user_a", first_name="A", last_name=None, is_bot=False)
    user_b = UserSnapshot(telegram_user_id=2002, username="user_b", first_name="B", last_name=None, is_bot=False)
    user_c = UserSnapshot(telegram_user_id=2003, username="user_c", first_name="C", last_name=None, is_bot=False)
    user_d = UserSnapshot(telegram_user_id=2004, username="user_d", first_name="D", last_name=None, is_bot=False)

    async with session_factory() as session:
        repo = SqlAlchemyActivityRepository(session)
        first_at = datetime(2026, 2, 10, 10, 0, tzinfo=timezone.utc)
        second_at = first_at + timedelta(days=2)

        first_proposal, error = await repo.create_marriage_proposal(
            chat=chat,
            proposer=user_a,
            target=user_b,
            kind="marriage",
            expires_at=first_at + timedelta(hours=1),
            event_at=first_at,
        )
        assert first_proposal is not None
        assert error is None
        _, first_marriage, error = await repo.respond_relationship_proposal(
            proposal_id=first_proposal.id,
            actor_user_id=user_b.telegram_user_id,
            accept=True,
            event_at=first_at,
        )
        assert first_marriage is not None
        assert error is None

        second_proposal, error = await repo.create_marriage_proposal(
            chat=chat,
            proposer=user_c,
            target=user_d,
            kind="marriage",
            expires_at=second_at + timedelta(hours=1),
            event_at=second_at,
        )
        assert second_proposal is not None
        assert error is None
        _, second_marriage, error = await repo.respond_relationship_proposal(
            proposal_id=second_proposal.id,
            actor_user_id=user_d.telegram_user_id,
            accept=True,
            event_at=second_at,
        )
        assert second_marriage is not None
        assert error is None

        marriages = await repo.list_active_marriages(chat_id=chat.telegram_chat_id)
        assert [item.id for item in marriages] == [first_marriage.id, second_marriage.id]

        await session.commit()

    await engine.dispose()


@pytest.mark.integration
@pytest.mark.asyncio
async def test_repository_keeps_marriage_history_after_divorce() -> None:
    database_url = os.getenv("TEST_DATABASE_URL")
    if not database_url:
        pytest.skip("TEST_DATABASE_URL is not set")

    engine = create_async_engine(database_url)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
        await conn.run_sync(Base.metadata.create_all)

    chat = ChatSnapshot(telegram_chat_id=-100511, chat_type="group", title="Marriage History")
    user_a = UserSnapshot(telegram_user_id=2101, username="user_a", first_name="A", last_name=None, is_bot=False)
    user_b = UserSnapshot(telegram_user_id=2102, username="user_b", first_name="B", last_name=None, is_bot=False)

    async with session_factory() as session:
        repo = SqlAlchemyActivityRepository(session)
        married_at = datetime(2026, 2, 12, 10, 0, tzinfo=timezone.utc)

        proposal, error = await repo.create_marriage_proposal(
            chat=chat,
            proposer=user_a,
            target=user_b,
            kind="marriage",
            expires_at=married_at + timedelta(hours=1),
            event_at=married_at,
        )
        assert proposal is not None
        assert error is None

        _, marriage, error = await repo.respond_relationship_proposal(
            proposal_id=proposal.id,
            actor_user_id=user_b.telegram_user_id,
            accept=True,
            event_at=married_at,
        )
        assert marriage is not None
        assert error is None

        dissolved = await repo.dissolve_marriage(user_id=user_a.telegram_user_id, chat_id=chat.telegram_chat_id)
        assert dissolved is not None
        assert dissolved.id == marriage.id

        assert await repo.get_active_marriage(user_id=user_a.telegram_user_id, chat_id=chat.telegram_chat_id) is None
        assert await repo.list_active_marriages(chat_id=chat.telegram_chat_id) == []

        row = (
            await session.execute(select(MarriageModel).where(MarriageModel.id == marriage.id))
        ).scalar_one()
        assert row.is_active is False
        assert row.ended_at is not None
        assert row.ended_by_user_id == user_a.telegram_user_id
        assert row.ended_reason == "initiated_by_user"

        await session.commit()

    await engine.dispose()


@pytest.mark.integration
@pytest.mark.asyncio
async def test_repository_applies_iris_import_and_merges_awards() -> None:
    database_url = os.getenv("TEST_DATABASE_URL")
    if not database_url:
        pytest.skip("TEST_DATABASE_URL is not set")

    engine = create_async_engine(database_url)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
        await conn.run_sync(Base.metadata.create_all)

    chat = ChatSnapshot(telegram_chat_id=-100700, chat_type="group", title="Iris Group")
    actor = UserSnapshot(telegram_user_id=501, username="admin501", first_name="Admin", last_name=None, is_bot=False)
    target = UserSnapshot(telegram_user_id=777, username="nigh_cord25", first_name="Kykold", last_name=None, is_bot=False)
    imported_at = datetime(2026, 3, 12, 10, 0, tzinfo=timezone.utc)

    async with session_factory() as session:
        repo = SqlAlchemyActivityRepository(session)

        await repo.upsert_activity(chat=chat, user=target, event_at=datetime(2026, 3, 1, 10, 0, tzinfo=timezone.utc))
        await repo.upsert_activity(chat=chat, user=target, event_at=datetime(2026, 3, 2, 10, 0, tzinfo=timezone.utc))
        await repo.add_user_chat_award(
            chat=chat,
            target=target,
            title="Старая награда",
            granted_by_user_id=actor.telegram_user_id,
            created_at=datetime(2026, 3, 2, 12, 0, tzinfo=timezone.utc),
        )

        state = await repo.apply_user_chat_iris_import(
            chat=chat,
            target=target,
            imported_by_user_id=actor.telegram_user_id,
            source_bot_username="iris_moon_bot",
            source_target_username="nigh_cord25",
            imported_at=imported_at,
            profile_text="profile raw",
            awards_text="awards raw",
            karma_base_all_time=14,
            first_seen_at=datetime(2026, 1, 18, 5, 0, tzinfo=timezone.utc),
            last_seen_at=datetime(2026, 3, 12, 9, 0, tzinfo=timezone.utc),
            activity_1d=4,
            activity_7d=11,
            activity_30d=30,
            activity_all=99,
            awards=[
                ("🎗₁ Ждун яйца", datetime(2026, 3, 1, 5, 0, tzinfo=timezone.utc)),
                ("🎗₁ Лучший влд", datetime(2026, 2, 1, 5, 0, tzinfo=timezone.utc)),
            ],
        )

        await session.flush()

        assert state.chat_id == chat.telegram_chat_id
        assert state.user_id == target.telegram_user_id
        assert state.karma_base_all_time == 14

        stats = await repo.get_user_stats(chat_id=chat.telegram_chat_id, user_id=target.telegram_user_id)
        assert stats is not None
        assert stats.message_count == 99
        assert stats.first_seen_at == datetime(2026, 1, 18, 5, 0, tzinfo=timezone.utc)
        assert stats.last_seen_at == datetime(2026, 3, 12, 9, 0, tzinfo=timezone.utc)

        daily_rows = (
            await session.execute(
                select(UserChatActivityDailyModel).where(
                    UserChatActivityDailyModel.chat_id == chat.telegram_chat_id,
                    UserChatActivityDailyModel.user_id == target.telegram_user_id,
                )
            )
        ).scalars().all()
        assert sum(row.message_count for row in daily_rows if row.activity_date >= imported_at.date()) == 4
        assert sum(row.message_count for row in daily_rows if row.activity_date >= imported_at.date() - timedelta(days=6)) == 11
        assert sum(row.message_count for row in daily_rows if row.activity_date >= imported_at.date() - timedelta(days=29)) == 30

        awards = await repo.list_user_chat_awards(chat_id=chat.telegram_chat_id, user_id=target.telegram_user_id, limit=10)
        assert len(awards) == 3
        assert {award.title for award in awards} >= {"Старая награда", "🎗₁ Ждун яйца", "🎗₁ Лучший влд"}

        history_rows = (
            await session.execute(
                select(UserChatIrisImportHistoryModel).where(
                    UserChatIrisImportHistoryModel.chat_id == chat.telegram_chat_id,
                    UserChatIrisImportHistoryModel.user_id == target.telegram_user_id,
                )
            )
        ).scalars().all()
        assert len(history_rows) == 1
        assert history_rows[0].archived_snapshot_json["activity_row"]["message_count"] == 2
        assert history_rows[0].archived_snapshot_json["minute_rows_summary"]["count"] >= 1
        assert len(history_rows[0].archived_snapshot_json["awards"]) == 1

        assert await repo.get_karma_value(
            chat_id=chat.telegram_chat_id,
            user_id=target.telegram_user_id,
            period="all",
            since=None,
        ) == 14
        assert await repo.get_karma_value(
            chat_id=chat.telegram_chat_id,
            user_id=target.telegram_user_id,
            period="7d",
            since=imported_at - timedelta(days=7),
        ) == 0
        activity_1d, _, _ = await repo.get_representation_stats(
            chat_id=chat.telegram_chat_id,
            user_id=target.telegram_user_id,
            since=imported_at - timedelta(days=1),
        )
        activity_7d, _, _ = await repo.get_representation_stats(
            chat_id=chat.telegram_chat_id,
            user_id=target.telegram_user_id,
            since=imported_at - timedelta(days=7),
        )
        assert activity_1d == 4
        assert activity_7d == 11

        leaderboard = await repo.get_leaderboard(
            chat_id=chat.telegram_chat_id,
            mode="karma",
            period="all",
            since=None,
            limit=10,
            karma_weight=1.0,
            activity_weight=0.0,
        )
        assert leaderboard
        assert leaderboard[0].user_id == target.telegram_user_id
        assert leaderboard[0].karma_value == 14

        day_leaderboard = await repo.get_leaderboard(
            chat_id=chat.telegram_chat_id,
            mode="activity",
            period="day",
            since=imported_at - timedelta(days=1),
            limit=10,
            karma_weight=0.0,
            activity_weight=1.0,
        )
        assert day_leaderboard
        assert day_leaderboard[0].user_id == target.telegram_user_id
        assert day_leaderboard[0].activity_value == 4

        seven_day_leaderboard = await repo.get_leaderboard(
            chat_id=chat.telegram_chat_id,
            mode="activity",
            period="7d",
            since=imported_at - timedelta(days=7),
            limit=10,
            karma_weight=0.0,
            activity_weight=1.0,
        )
        assert seven_day_leaderboard
        assert seven_day_leaderboard[0].user_id == target.telegram_user_id
        assert seven_day_leaderboard[0].activity_value == 11

        await session.commit()

    await engine.dispose()


@pytest.mark.integration
@pytest.mark.asyncio
async def test_relationships_are_isolated_per_chat() -> None:
    database_url = os.getenv("TEST_DATABASE_URL")
    if not database_url:
        pytest.skip("TEST_DATABASE_URL is not set")

    engine = create_async_engine(database_url)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
        await conn.run_sync(Base.metadata.create_all)

    chat_one = ChatSnapshot(telegram_chat_id=-100610, chat_type="group", title="Relationships One")
    chat_two = ChatSnapshot(telegram_chat_id=-100611, chat_type="group", title="Relationships Two")
    user_a = UserSnapshot(telegram_user_id=1101, username="user_a", first_name="A", last_name=None, is_bot=False)
    user_b = UserSnapshot(telegram_user_id=1102, username="user_b", first_name="B", last_name=None, is_bot=False)

    async with session_factory() as session:
        repo = SqlAlchemyActivityRepository(session)
        now = datetime(2026, 2, 17, 12, 0, tzinfo=timezone.utc)

        proposal_one, error = await repo.create_marriage_proposal(
            chat=chat_one,
            proposer=user_a,
            target=user_b,
            kind="marriage",
            expires_at=now + timedelta(hours=1),
            event_at=now,
        )
        assert proposal_one is not None
        assert error is None

        _, marriage_one, error = await repo.respond_relationship_proposal(
            proposal_id=proposal_one.id,
            actor_user_id=user_b.telegram_user_id,
            accept=True,
            event_at=now + timedelta(minutes=1),
        )
        assert error is None
        assert marriage_one is not None
        assert marriage_one.chat_id == chat_one.telegram_chat_id

        assert await repo.get_active_marriage(user_id=user_a.telegram_user_id, chat_id=chat_two.telegram_chat_id) is None
        assert await repo.dissolve_marriage(user_id=user_a.telegram_user_id, chat_id=chat_two.telegram_chat_id) is None
        assert await repo.get_active_marriage(user_id=user_a.telegram_user_id, chat_id=chat_one.telegram_chat_id) is not None

        proposal_two, error = await repo.create_marriage_proposal(
            chat=chat_two,
            proposer=user_a,
            target=user_b,
            kind="marriage",
            expires_at=now + timedelta(hours=2),
            event_at=now + timedelta(minutes=2),
        )
        assert proposal_two is not None
        assert error is None

        _, marriage_two, error = await repo.respond_relationship_proposal(
            proposal_id=proposal_two.id,
            actor_user_id=user_b.telegram_user_id,
            accept=True,
            event_at=now + timedelta(minutes=3),
        )
        assert error is None
        assert marriage_two is not None
        assert marriage_two.chat_id == chat_two.telegram_chat_id
        assert marriage_two.id != marriage_one.id

        loaded_one = await repo.get_active_marriage(user_id=user_a.telegram_user_id, chat_id=chat_one.telegram_chat_id)
        loaded_two = await repo.get_active_marriage(user_id=user_a.telegram_user_id, chat_id=chat_two.telegram_chat_id)
        assert loaded_one is not None
        assert loaded_two is not None
        assert loaded_one.id == marriage_one.id
        assert loaded_two.id == marriage_two.id

        await session.commit()

    await engine.dispose()


@pytest.mark.integration
@pytest.mark.asyncio
async def test_repository_inline_private_messages_and_shared_lookup() -> None:
    database_url = os.getenv("TEST_DATABASE_URL")
    if not database_url:
        pytest.skip("TEST_DATABASE_URL is not set")

    engine = create_async_engine(database_url)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
        await conn.run_sync(Base.metadata.create_all)

    shared_chat = ChatSnapshot(telegram_chat_id=-100777, chat_type="group", title="Shared Group")
    sender = UserSnapshot(telegram_user_id=3001, username="sender_u", first_name="Sender", last_name=None, is_bot=False)
    receiver = UserSnapshot(telegram_user_id=3002, username="receiver_u", first_name="Receiver", last_name=None, is_bot=False)
    receiver_two = UserSnapshot(telegram_user_id=3004, username="receiver_two_u", first_name="Receiver2", last_name=None, is_bot=False)
    foreign_user = UserSnapshot(telegram_user_id=3003, username="foreign_u", first_name="Foreign", last_name=None, is_bot=False)
    foreign_chat = ChatSnapshot(telegram_chat_id=-100778, chat_type="group", title="Foreign Group")

    async with session_factory() as session:
        repo = SqlAlchemyActivityRepository(session)
        now = datetime(2026, 2, 18, 18, 0, tzinfo=timezone.utc)

        await repo.upsert_activity(chat=shared_chat, user=sender, event_at=now)
        await repo.upsert_activity(chat=shared_chat, user=receiver, event_at=now + timedelta(minutes=1))
        await repo.set_chat_display_name(chat=shared_chat, user=receiver, display_name="ReceiverLocal")
        await repo.upsert_activity(chat=shared_chat, user=receiver_two, event_at=now + timedelta(minutes=1, seconds=30))
        await repo.set_chat_display_name(chat=shared_chat, user=receiver_two, display_name="ReceiverTwoLocal")

        await repo.upsert_activity(chat=foreign_chat, user=foreign_user, event_at=now + timedelta(minutes=2))

        resolved = await repo.find_shared_group_user_by_username(
            sender_user_id=sender.telegram_user_id,
            username="@receiver_u",
        )
        assert resolved is not None
        assert resolved.telegram_user_id == receiver.telegram_user_id
        assert resolved.chat_display_name == "ReceiverLocal"

        unresolved = await repo.find_shared_group_user_by_username(
            sender_user_id=sender.telegram_user_id,
            username="@foreign_u",
        )
        assert unresolved is None

        created = await repo.create_inline_private_message(
            id="123e4567-e89b-12d3-a456-426614174000",
            chat_id=None,
            chat_instance=None,
            sender_id=sender.telegram_user_id,
            receiver_ids=[receiver.telegram_user_id],
            receiver_usernames=["receiver_u"],
            text="секретный текст",
            created_at=now + timedelta(minutes=3),
        )
        assert created.id == "123e4567-e89b-12d3-a456-426614174000"
        assert created.chat_id is None
        assert created.chat_instance is None
        assert created.receiver_ids == (receiver.telegram_user_id,)
        assert created.receiver_usernames == ("receiver_u",)

        loaded = await repo.get_inline_private_message(id=created.id)
        assert loaded is not None
        assert loaded.text == "секретный текст"
        assert loaded.receiver_usernames == ("receiver_u",)

        updated = await repo.set_inline_private_message_context(
            id=created.id,
            chat_id=shared_chat.telegram_chat_id,
            chat_instance="chat-instance-1",
        )
        assert updated is True

        loaded_after = await repo.get_inline_private_message(id=created.id)
        assert loaded_after is not None
        assert loaded_after.chat_id == shared_chat.telegram_chat_id
        assert loaded_after.chat_instance == "chat-instance-1"

        await repo.create_inline_private_message(
            id="123e4567-e89b-12d3-a456-426614174001",
            chat_id=None,
            chat_instance=None,
            sender_id=sender.telegram_user_id,
            receiver_ids=[receiver_two.telegram_user_id],
            receiver_usernames=["receiver_two_u"],
            text="второе сообщение",
            created_at=now + timedelta(minutes=4),
        )
        await repo.create_inline_private_message(
            id="123e4567-e89b-12d3-a456-426614174002",
            chat_id=None,
            chat_instance=None,
            sender_id=sender.telegram_user_id,
            receiver_ids=[receiver.telegram_user_id],
            receiver_usernames=["receiver_u"],
            text="третье сообщение",
            created_at=now + timedelta(minutes=5),
        )

        recent_receivers = await repo.list_recent_inline_private_receivers(
            sender_user_id=sender.telegram_user_id,
            limit=10,
        )
        assert [item.telegram_user_id for item in recent_receivers] == [
            receiver.telegram_user_id,
            receiver_two.telegram_user_id,
        ]
        assert recent_receivers[0].chat_display_name == "ReceiverLocal"
        assert recent_receivers[1].chat_display_name == "ReceiverTwoLocal"

        recent_usernames = await repo.list_recent_inline_private_receiver_usernames(
            sender_user_id=sender.telegram_user_id,
            limit=10,
        )
        assert recent_usernames == ["receiver_u", "receiver_two_u"]

        await session.commit()

    await engine.dispose()
