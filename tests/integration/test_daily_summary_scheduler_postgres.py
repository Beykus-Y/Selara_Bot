"""Integration tests for the daily summary orchestration (attempt_daily_summary_run):
claim -> generate -> finalize -> send, and the resend/skip paths around it. Uses a
real Postgres for the repository layer (claim atomicity, message counting, run
state) and a fake LlmClient (no network) to keep this fast and deterministic.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from selara.application.daily_summary.schemas import MergedTheme, MergedThemeList, SegmentTopicCard, SegmentTopicCardList
from selara.domain.entities import ChatSnapshot, UserSnapshot
from selara.infrastructure.db.base import Base
from selara.infrastructure.db.models import MessageArchiveModel
from selara.infrastructure.db.repositories import SqlAlchemyActivityRepository
from selara.presentation.daily_summary import attempt_daily_summary_run

_CHAT_ID = -100555
_USER_ID = 1001
_NOW = datetime(2026, 9, 3, 7, 0, tzinfo=timezone.utc)


async def _database():
    database_url = os.getenv("TEST_DATABASE_URL")
    if not database_url:
        pytest.skip("TEST_DATABASE_URL is not set")
    engine = create_async_engine(database_url)
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.drop_all)
        await connection.run_sync(Base.metadata.create_all)
    return engine, async_sessionmaker(engine, expire_on_commit=False)


async def _seed_chat(session_factory, *, message_count: int, min_messages: int) -> None:
    async with session_factory() as session:
        repo = SqlAlchemyActivityRepository(session)
        chat = ChatSnapshot(telegram_chat_id=_CHAT_ID, chat_type="supergroup", title="Test Chat")
        await repo._upsert_chat(chat)
        await repo._upsert_user(
            UserSnapshot(telegram_user_id=_USER_ID, username="vasya", first_name="Vasya", last_name=None, is_bot=False)
        )
        await repo.upsert_chat_settings(
            chat=chat,
            values={
                "daily_summary_enabled": True,
                "daily_summary_min_messages": min_messages,
                "daily_summary_style": "neutral",
                "save_message": True,
            },
        )
        rows = []
        for i in range(message_count):
            sent_at = _NOW - timedelta(hours=1) + timedelta(minutes=i)
            rows.append(
                MessageArchiveModel(
                    chat_id=_CHAT_ID,
                    user_id=_USER_ID,
                    telegram_message_id=i + 1,
                    snapshot_kind="created",
                    snapshot_at=sent_at,
                    sent_at=sent_at,
                    message_type="text",
                    text=f"сообщение {i}",
                    raw_message_json={"message_id": i + 1},
                    snapshot_hash=f"hash-{i}",
                )
            )
        session.add_all(rows)
        await session.commit()


@dataclass
class _FakeLlmClient:
    last_usage: tuple = (10, 5)
    last_model: str = "gpt-4o-mini"
    _structured_calls: int = field(default=0, init=False)

    async def chat_structured(self, messages, *, response_model, max_tokens=None):
        self._structured_calls += 1
        if response_model is SegmentTopicCardList:
            return SegmentTopicCardList(
                topics=[SegmentTopicCard(title="Разговор", start_message_id=1, end_message_id=2, blurb="Поболтали.")]
            )
        return MergedThemeList(
            themes=[MergedTheme(title="Разговор", source_card_indexes=[0], blurb="Итог.", importance=3)]
        )

    async def chat_with_tools(self, messages, tools, *, max_tokens=None):
        message = SimpleNamespace(content="[]", tool_calls=None)
        return SimpleNamespace(choices=[SimpleNamespace(message=message)])

    async def chat_simple(self, messages, *, max_tokens=None):
        return "Итоги дня: сегодня поболтали в чате."


def _fake_bot() -> SimpleNamespace:
    return SimpleNamespace(send_message=AsyncMock())


@pytest.mark.integration
@pytest.mark.asyncio
async def test_attempt_daily_summary_run_full_cycle_sends_and_marks_sent() -> None:
    engine, session_factory = await _database()
    try:
        await _seed_chat(session_factory, message_count=60, min_messages=50)
        bot = _fake_bot()
        llm_client = _FakeLlmClient()
        chat = ChatSnapshot(telegram_chat_id=_CHAT_ID, chat_type="supergroup", title="Test Chat")

        outcome = await attempt_daily_summary_run(
            bot=bot,
            session_factory=session_factory,
            llm_client=llm_client,
            chat=chat,
            trigger="manual",
            window_to=_NOW,
            summary_date=_NOW.date(),
            now_utc=_NOW,
        )

        assert outcome.sent is True
        assert outcome.reason == "sent"
        bot.send_message.assert_awaited_once()
        assert "поболтали" in bot.send_message.await_args.kwargs["text"]

        async with session_factory() as session:
            repo = SqlAlchemyActivityRepository(session)
            run = await repo.get_daily_summary_run(chat_id=_CHAT_ID, summary_date=_NOW.date(), trigger="manual")
        assert run is not None
        assert run.status == "sent"
        assert run.pipeline_cost_usd > 0
        assert run.diagnostics_json is not None
        assert run.diagnostics_json["message_count"] == 60
        assert run.diagnostics_json["cards_before_merge_count"] >= 1
    finally:
        await engine.dispose()


@pytest.mark.integration
@pytest.mark.asyncio
async def test_manual_trigger_works_even_when_scheduled_automation_is_disabled() -> None:
    # /summary must work regardless of the daily_summary_enabled automation toggle --
    # only the scheduled path is gated on that setting (see docs/DAILY_SUMMARY_TODO.md)
    engine, session_factory = await _database()
    try:
        await _seed_chat(session_factory, message_count=60, min_messages=50)
        async with session_factory() as session:
            repo = SqlAlchemyActivityRepository(session)
            chat = ChatSnapshot(telegram_chat_id=_CHAT_ID, chat_type="supergroup", title="Test Chat")
            await repo.upsert_chat_settings(chat=chat, values={"daily_summary_enabled": False})
            await session.commit()

        bot = _fake_bot()
        llm_client = _FakeLlmClient()
        chat = ChatSnapshot(telegram_chat_id=_CHAT_ID, chat_type="supergroup", title="Test Chat")

        outcome = await attempt_daily_summary_run(
            bot=bot, session_factory=session_factory, llm_client=llm_client, chat=chat,
            trigger="manual", window_to=_NOW, summary_date=_NOW.date(), now_utc=_NOW,
        )

        assert outcome.sent is True
    finally:
        await engine.dispose()


@pytest.mark.integration
@pytest.mark.asyncio
async def test_scheduled_trigger_is_blocked_when_automation_is_disabled() -> None:
    engine, session_factory = await _database()
    try:
        await _seed_chat(session_factory, message_count=60, min_messages=50)
        async with session_factory() as session:
            repo = SqlAlchemyActivityRepository(session)
            chat = ChatSnapshot(telegram_chat_id=_CHAT_ID, chat_type="supergroup", title="Test Chat")
            await repo.upsert_chat_settings(chat=chat, values={"daily_summary_enabled": False})
            await session.commit()

        bot = _fake_bot()
        llm_client = _FakeLlmClient()
        chat = ChatSnapshot(telegram_chat_id=_CHAT_ID, chat_type="supergroup", title="Test Chat")

        outcome = await attempt_daily_summary_run(
            bot=bot, session_factory=session_factory, llm_client=llm_client, chat=chat,
            trigger="scheduled", window_to=_NOW, summary_date=_NOW.date(), now_utc=_NOW,
        )

        assert outcome.sent is False
        assert outcome.reason == "not_eligible:disabled"
    finally:
        await engine.dispose()


@pytest.mark.integration
@pytest.mark.asyncio
async def test_attempt_daily_summary_run_skips_below_message_threshold() -> None:
    engine, session_factory = await _database()
    try:
        await _seed_chat(session_factory, message_count=5, min_messages=50)
        bot = _fake_bot()
        llm_client = _FakeLlmClient()
        chat = ChatSnapshot(telegram_chat_id=_CHAT_ID, chat_type="supergroup", title="Test Chat")

        outcome = await attempt_daily_summary_run(
            bot=bot,
            session_factory=session_factory,
            llm_client=llm_client,
            chat=chat,
            trigger="manual",
            window_to=_NOW,
            summary_date=_NOW.date(),
            now_utc=_NOW,
        )

        assert outcome.sent is False
        assert outcome.reason == "not_eligible:not_enough_messages"
        bot.send_message.assert_not_awaited()

        async with session_factory() as session:
            repo = SqlAlchemyActivityRepository(session)
            run = await repo.get_daily_summary_run(chat_id=_CHAT_ID, summary_date=_NOW.date(), trigger="manual")
        assert run is None  # never even claimed -- no wasted LLM cost
    finally:
        await engine.dispose()


@pytest.mark.integration
@pytest.mark.asyncio
async def test_second_manual_run_same_day_is_a_no_op_after_first_sends() -> None:
    engine, session_factory = await _database()
    try:
        await _seed_chat(session_factory, message_count=60, min_messages=50)
        bot = _fake_bot()
        llm_client = _FakeLlmClient()
        chat = ChatSnapshot(telegram_chat_id=_CHAT_ID, chat_type="supergroup", title="Test Chat")

        first = await attempt_daily_summary_run(
            bot=bot, session_factory=session_factory, llm_client=llm_client, chat=chat,
            trigger="manual", window_to=_NOW, summary_date=_NOW.date(), now_utc=_NOW,
        )
        assert first.sent is True

        second = await attempt_daily_summary_run(
            bot=bot, session_factory=session_factory, llm_client=llm_client, chat=chat,
            trigger="manual", window_to=_NOW, summary_date=_NOW.date(), now_utc=_NOW + timedelta(minutes=1),
        )

        assert second.sent is False
        assert second.reason == "already_run_today"
        bot.send_message.assert_awaited_once()  # still just the one send from `first`
    finally:
        await engine.dispose()
