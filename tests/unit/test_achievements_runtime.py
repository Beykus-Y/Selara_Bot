from datetime import datetime, timedelta, timezone
import importlib.util
from pathlib import Path

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from selara.application.achievements import (
    AchievementAwardService,
    AchievementCatalogService,
    AchievementConditionEvaluator,
    AchievementOrchestrator,
)
from selara.domain.entities import ChatSnapshot, UserSnapshot
from selara.infrastructure.db.base import Base
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
