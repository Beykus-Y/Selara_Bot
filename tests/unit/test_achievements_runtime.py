from datetime import datetime, timedelta, timezone
import importlib.util
from pathlib import Path

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from selara.application.achievements import (
    AchievementAwardService,
    AchievementCatalogService,
    AchievementConditionEvaluator,
    AchievementOrchestrator,
)
from selara.domain.entities import ChatSnapshot, UserSnapshot
from selara.infrastructure.db.base import Base
from selara.infrastructure.db.models import (
    ChatAchievementStatsModel,
    ChatMetricsModel,
    GlobalAchievementStatsModel,
    RelationshipGraphModel,
)
from selara.infrastructure.db.repositories import SqlAlchemyActivityRepository

pytestmark = pytest.mark.skipif(importlib.util.find_spec("aiosqlite") is None, reason="aiosqlite is not installed")


@pytest.mark.asyncio
async def test_achievement_orchestrator_awards_first_message_and_updates_stats() -> None:
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    catalog = AchievementCatalogService.load(Path("src/selara/core/achievements.json"))

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    async with session_factory() as session:
        repo = SqlAlchemyActivityRepository(session)
        orchestrator = AchievementOrchestrator(
            catalog=catalog,
            evaluator=AchievementConditionEvaluator(),
            award_service=AchievementAwardService(session, catalog),
            repo=repo,
        )
        chat = ChatSnapshot(telegram_chat_id=1, chat_type="group", title="Test")
        user = UserSnapshot(telegram_user_id=10, username="alice", first_name="Alice", last_name=None, is_bot=False)
        event_at = datetime(2026, 3, 9, 12, 0, tzinfo=timezone.utc)

        await repo.upsert_activity(chat=chat, user=user, event_at=event_at)
        results = await orchestrator.process_message(chat_id=chat.telegram_chat_id, user_id=user.telegram_user_id, event_at=event_at)

        assert [item.achievement_id for item in results] == ["first_message"]

        chat_achievements = await repo.list_user_chat_achievements(chat_id=chat.telegram_chat_id, user_id=user.telegram_user_id)
        assert [item.achievement_id for item in chat_achievements] == ["first_message"]

        chat_stats = await repo.get_chat_achievement_stats_map(chat_id=chat.telegram_chat_id)
        assert chat_stats["first_message"] == (1, 100.0)

        await session.commit()

    await engine.dispose()


@pytest.mark.asyncio
async def test_achievement_orchestrator_awards_streak_and_global_collection() -> None:
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    catalog = AchievementCatalogService.load(Path("src/selara/core/achievements.json"))

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    async with session_factory() as session:
        repo = SqlAlchemyActivityRepository(session)
        orchestrator = AchievementOrchestrator(
            catalog=catalog,
            evaluator=AchievementConditionEvaluator(),
            award_service=AchievementAwardService(session, catalog),
            repo=repo,
        )
        chat = ChatSnapshot(telegram_chat_id=1, chat_type="group", title="Test")
        user = UserSnapshot(telegram_user_id=10, username="alice", first_name="Alice", last_name=None, is_bot=False)
        start = datetime(2026, 3, 1, 12, 0, tzinfo=timezone.utc)

        for offset in range(7):
            event_at = start + timedelta(days=offset)
            await repo.upsert_activity(chat=chat, user=user, event_at=event_at)
            await orchestrator.process_message(chat_id=chat.telegram_chat_id, user_id=user.telegram_user_id, event_at=event_at)

        for _ in range(93):
            event_at = start + timedelta(days=6, minutes=1)
            await repo.upsert_activity(chat=chat, user=user, event_at=event_at)
        results = await orchestrator.process_message(
            chat_id=chat.telegram_chat_id,
            user_id=user.telegram_user_id,
            event_at=start + timedelta(days=6, minutes=2),
        )

        awarded_ids = {item.achievement_id for item in results}
        assert "chat_100_messages" in awarded_ids
        assert "global_3_achievements" in awarded_ids

        global_achievements = await repo.list_user_global_achievements(user_id=user.telegram_user_id)
        assert [item.achievement_id for item in global_achievements] == ["global_3_achievements"]

        await session.commit()

    await engine.dispose()


@pytest.mark.asyncio
async def test_achievement_orchestrator_awards_daily_message_threshold_in_chat() -> None:
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    catalog = AchievementCatalogService.load(Path("src/selara/core/achievements.json"))

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    async with session_factory() as session:
        repo = SqlAlchemyActivityRepository(session)
        orchestrator = AchievementOrchestrator(
            catalog=catalog,
            evaluator=AchievementConditionEvaluator(),
            award_service=AchievementAwardService(session, catalog),
            repo=repo,
        )
        chat = ChatSnapshot(telegram_chat_id=1, chat_type="group", title="Test")
        user = UserSnapshot(telegram_user_id=10, username="alice", first_name="Alice", last_name=None, is_bot=False)
        event_at = datetime(2026, 3, 9, 12, 0, tzinfo=timezone.utc)

        for _ in range(100):
            await repo.upsert_activity(chat=chat, user=user, event_at=event_at)
        results = await orchestrator.process_message(chat_id=chat.telegram_chat_id, user_id=user.telegram_user_id, event_at=event_at)

        awarded_ids = {item.achievement_id for item in results}
        assert "chat_100_messages_day" in awarded_ids

        await session.commit()

    await engine.dispose()


@pytest.mark.asyncio
async def test_chat_achievement_checks_stay_within_chat_scope_and_recompute_percentages() -> None:
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    catalog = AchievementCatalogService.load(Path("src/selara/core/achievements.json"))

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    async with session_factory() as session:
        repo = SqlAlchemyActivityRepository(session)
        orchestrator = AchievementOrchestrator(
            catalog=catalog,
            evaluator=AchievementConditionEvaluator(),
            award_service=AchievementAwardService(session, catalog),
            repo=repo,
        )
        chat_one = ChatSnapshot(telegram_chat_id=1, chat_type="group", title="One")
        chat_two = ChatSnapshot(telegram_chat_id=2, chat_type="group", title="Two")
        owner = UserSnapshot(telegram_user_id=10, username="owner", first_name="Owner", last_name=None, is_bot=False)
        pet = UserSnapshot(telegram_user_id=11, username="pet", first_name="Pet", last_name=None, is_bot=False)
        event_at = datetime(2026, 3, 9, 12, 0, tzinfo=timezone.utc)

        await repo.upsert_activity(chat=chat_one, user=owner, event_at=event_at)
        await repo.upsert_activity(chat=chat_one, user=pet, event_at=event_at)
        await repo.upsert_activity(chat=chat_two, user=owner, event_at=event_at)
        await repo.upsert_activity(chat=chat_two, user=pet, event_at=event_at)
        session.add(
            RelationshipGraphModel(
                id=1,
                chat_id=chat_one.telegram_chat_id,
                user_a=owner.telegram_user_id,
                user_b=pet.telegram_user_id,
                relation_type="pet",
                created_by_user_id=owner.telegram_user_id,
            )
        )
        await session.flush()

        results_chat_two = await orchestrator.process_refresh(
            chat_id=chat_two.telegram_chat_id,
            user_id=owner.telegram_user_id,
            event_at=event_at,
            event_type="family_pet_accepted",
        )
        assert "global_pet_owner" not in {item.achievement_id for item in results_chat_two}

        results_chat_one = await orchestrator.process_refresh(
            chat_id=chat_one.telegram_chat_id,
            user_id=owner.telegram_user_id,
            event_at=event_at,
            event_type="family_pet_accepted",
        )
        assert "global_pet_owner" in {item.achievement_id for item in results_chat_one}

        chat_stats_row = (
            await session.execute(
                select(ChatAchievementStatsModel).where(
                    ChatAchievementStatsModel.chat_id == chat_one.telegram_chat_id,
                    ChatAchievementStatsModel.achievement_id == "global_pet_owner",
                )
            )
        ).scalar_one()
        chat_stats_row.active_members_base_count = 0
        chat_stats_row.holders_percent = 0
        chat_metrics_row = await session.get(ChatMetricsModel, chat_one.telegram_chat_id)
        assert chat_metrics_row is not None
        chat_metrics_row.active_members_count = 0

        global_stats_row = GlobalAchievementStatsModel(
            achievement_id="global_3_achievements",
            holders_count=1,
            holders_percent=0,
            global_base_count=0,
        )
        session.add(global_stats_row)
        await session.flush()

        chat_stats = await repo.get_chat_achievement_stats_map(chat_id=chat_one.telegram_chat_id)
        global_stats = await repo.get_global_achievement_stats_map()

        assert chat_stats["global_pet_owner"] == (1, 50.0)
        assert global_stats["global_3_achievements"] == (1, 50.0)

        await session.commit()

    await engine.dispose()
