from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timedelta, timezone
import importlib.util
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from selara.core.chat_settings import default_chat_settings
from selara.core.config import Settings
from selara.domain.entities import ChatSnapshot, UserSnapshot
from selara.infrastructure.db.base import Base
from selara.infrastructure.db.models import ChatActivityEventSyncStateModel
from selara.infrastructure.db.repositories import SqlAlchemyActivityRepository
from selara.presentation.handlers.settings_common import settings_to_dict
from selara.presentation.interesting_facts import InterestingFactCatalog, InterestingFactsScheduler

pytestmark = pytest.mark.skipif(importlib.util.find_spec("aiosqlite") is None, reason="aiosqlite is not installed")


def _settings() -> Settings:
    return Settings.model_validate(
        {
            "BOT_TOKEN": "123456:TEST",
            "DATABASE_URL": "postgresql+asyncpg://user:pass@localhost:5432/selara_test",
        }
    )


def _fact_settings():
    return replace(
        default_chat_settings(_settings()),
        interesting_facts_enabled=True,
        interesting_facts_interval_minutes=180,
        interesting_facts_target_messages=150,
        interesting_facts_sleep_cap_minutes=1440,
    )


@pytest.mark.asyncio
async def test_interesting_fact_settings_roundtrip() -> None:
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    session_factory = async_sessionmaker(engine, expire_on_commit=False)

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    chat = ChatSnapshot(telegram_chat_id=-100500, chat_type="group", title="Facts")
    expected = _fact_settings()

    async with session_factory() as session:
        repo = SqlAlchemyActivityRepository(session)
        await repo.upsert_chat_settings(chat=chat, values=settings_to_dict(expected))
        loaded = await repo.get_chat_settings(chat_id=chat.telegram_chat_id)

        assert loaded is not None
        assert loaded.interesting_facts_enabled is True
        assert loaded.interesting_facts_interval_minutes == 180
        assert loaded.interesting_facts_target_messages == 150
        assert loaded.interesting_facts_sleep_cap_minutes == 1440

    await engine.dispose()


@pytest.mark.asyncio
async def test_interesting_fact_state_roundtrip_and_human_message_count() -> None:
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    session_factory = async_sessionmaker(engine, expire_on_commit=False)

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    chat = ChatSnapshot(telegram_chat_id=-100777, chat_type="group", title="Facts")
    human = UserSnapshot(telegram_user_id=1, username="human", first_name="Human", last_name=None, is_bot=False)
    bot_user = UserSnapshot(telegram_user_id=2, username="bot", first_name="Bot", last_name=None, is_bot=True)
    now = datetime(2026, 3, 19, 12, 0, tzinfo=timezone.utc)

    async with session_factory() as session:
        repo = SqlAlchemyActivityRepository(session)
        await repo.upsert_activity(chat=chat, user=human, event_at=now - timedelta(hours=5))
        await repo.upsert_activity(chat=chat, user=human, event_at=now - timedelta(hours=4))
        await repo.upsert_activity(chat=chat, user=bot_user, event_at=now - timedelta(hours=3))
        await repo.upsert_chat_interesting_fact_state(
            chat=chat,
            last_sent_at=now - timedelta(hours=6),
            last_fact_id="fact_alpha",
            used_fact_ids=["fact_alpha", "fact_beta", "fact_alpha", ""],
        )

        state = await repo.get_chat_interesting_fact_state(chat_id=chat.telegram_chat_id)
        minute_count = await repo.count_human_messages_since(chat_id=chat.telegram_chat_id, since=now - timedelta(hours=6))

        assert state is not None
        assert state.last_fact_id == "fact_alpha"
        assert state.used_fact_ids == ("fact_alpha", "fact_beta")
        assert minute_count == 2

        session.add(ChatActivityEventSyncStateModel(chat_id=chat.telegram_chat_id, status="synced"))
        await session.flush()

        synced_repo = SqlAlchemyActivityRepository(session)
        event_count = await synced_repo.count_human_messages_since(
            chat_id=chat.telegram_chat_id,
            since=now - timedelta(hours=6),
        )
        assert event_count == 2

    await engine.dispose()


@pytest.mark.asyncio
async def test_interesting_fact_scheduler_does_not_persist_state_on_send_error(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    session_factory = async_sessionmaker(engine, expire_on_commit=False)

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    chat = ChatSnapshot(telegram_chat_id=-100888, chat_type="group", title="Facts")
    human = UserSnapshot(telegram_user_id=1, username="human", first_name="Human", last_name=None, is_bot=False)
    now = datetime(2026, 3, 19, 12, 0, tzinfo=timezone.utc)

    async with session_factory() as session:
        repo = SqlAlchemyActivityRepository(session)
        await repo.upsert_chat_settings(chat=chat, values=settings_to_dict(_fact_settings()))
        await repo.upsert_activity(chat=chat, user=human, event_at=now - timedelta(hours=5))
        await session.commit()

    path = tmp_path / "facts.json"
    path.write_text('["Тестовый факт"]', encoding="utf-8")
    bot = SimpleNamespace(send_message=AsyncMock(side_effect=RuntimeError("send failed")))
    scheduler = InterestingFactsScheduler(
        bot=bot,
        session_factory=session_factory,
        catalog=InterestingFactCatalog(path),
    )

    monkeypatch.setattr(
        "selara.presentation.interesting_facts.GAME_STORE.get_active_game_for_chat",
        AsyncMock(return_value=None),
    )

    sent = await scheduler.run_once(now=now)

    assert sent == 0
    bot.send_message.assert_awaited_once()

    async with session_factory() as session:
        repo = SqlAlchemyActivityRepository(session)
        state = await repo.get_chat_interesting_fact_state(chat_id=chat.telegram_chat_id)
        assert state is None

    await engine.dispose()
