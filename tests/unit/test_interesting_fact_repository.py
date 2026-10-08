from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timedelta, timezone
import importlib.util
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from aiogram.exceptions import TelegramBadRequest, TelegramNetworkError
from aiogram.methods import SendMessage
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from selara.core.chat_settings import default_chat_settings
from selara.core.config import Settings
from selara.domain.entities import ChatSnapshot, UserSnapshot
from selara.infrastructure.db.base import Base
from selara.infrastructure.db.models import ChatActivityEventSyncStateModel, ChatInterestingFactDeliveryModel
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


@pytest.mark.asyncio
async def test_interesting_fact_is_not_resent_when_state_persist_fails_after_send(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    session_factory = async_sessionmaker(engine, expire_on_commit=False)

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    chat = ChatSnapshot(telegram_chat_id=-100901, chat_type="group", title="Facts")
    human = UserSnapshot(telegram_user_id=1, username="human", first_name="Human", last_name=None, is_bot=False)
    now = datetime(2026, 3, 19, 12, 0, tzinfo=timezone.utc)

    async with session_factory() as session:
        repo = SqlAlchemyActivityRepository(session)
        await repo.upsert_chat_settings(chat=chat, values=settings_to_dict(_fact_settings()))
        await repo.upsert_activity(chat=chat, user=human, event_at=now - timedelta(hours=5))
        await session.commit()

    path = tmp_path / "facts.json"
    path.write_text('["Тестовый факт"]', encoding="utf-8")
    bot = SimpleNamespace(send_message=AsyncMock(return_value=SimpleNamespace(message_id=501)))
    scheduler = InterestingFactsScheduler(
        bot=bot,
        session_factory=session_factory,
        catalog=InterestingFactCatalog(path),
    )
    monkeypatch.setattr(
        "selara.presentation.interesting_facts.GAME_STORE.get_active_game_for_chat",
        AsyncMock(return_value=None),
    )

    original_upsert = SqlAlchemyActivityRepository.upsert_chat_interesting_fact_state

    async def failing_upsert(self, **kwargs):
        raise RuntimeError("db unavailable after send")

    monkeypatch.setattr(SqlAlchemyActivityRepository, "upsert_chat_interesting_fact_state", failing_upsert)
    assert await scheduler.run_once(now=now) == 1

    # The DB recovers; the next tick is still inside the 180 minute interval.
    monkeypatch.setattr(SqlAlchemyActivityRepository, "upsert_chat_interesting_fact_state", original_upsert)
    assert await scheduler.run_once(now=now + timedelta(minutes=5)) == 0
    bot.send_message.assert_awaited_once()

    await engine.dispose()


async def _seed_fact_chat(session_factory, chat: ChatSnapshot, now: datetime) -> None:
    human = UserSnapshot(telegram_user_id=1, username="human", first_name="Human", last_name=None, is_bot=False)
    async with session_factory() as session:
        repo = SqlAlchemyActivityRepository(session)
        await repo.upsert_chat_settings(chat=chat, values=settings_to_dict(_fact_settings()))
        await repo.upsert_activity(chat=chat, user=human, event_at=now - timedelta(hours=5))
        await session.commit()


def _write_facts(tmp_path, texts: list[str]) -> InterestingFactCatalog:
    path = tmp_path / "facts.json"
    path.write_text(json.dumps(texts, ensure_ascii=False), encoding="utf-8")
    return InterestingFactCatalog(path)


@pytest.mark.asyncio
async def test_interesting_fact_telegram_rejection_keeps_state_and_retries_next_tick(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    chat = ChatSnapshot(telegram_chat_id=-100902, chat_type="group", title="Facts")
    now = datetime(2026, 3, 19, 12, 0, tzinfo=timezone.utc)
    await _seed_fact_chat(session_factory, chat, now)

    rejection = TelegramBadRequest(
        method=SendMessage(chat_id=chat.telegram_chat_id, text="x"),
        message="chat not found",
    )
    bot = SimpleNamespace(
        send_message=AsyncMock(side_effect=[rejection, SimpleNamespace(message_id=777)]),
    )
    scheduler = InterestingFactsScheduler(
        bot=bot,
        session_factory=session_factory,
        catalog=_write_facts(tmp_path, ["Тестовый факт"]),
    )
    monkeypatch.setattr(
        "selara.presentation.interesting_facts.GAME_STORE.get_active_game_for_chat",
        AsyncMock(return_value=None),
    )

    assert await scheduler.run_once(now=now) == 0
    async with session_factory() as session:
        repo = SqlAlchemyActivityRepository(session)
        assert await repo.get_chat_interesting_fact_state(chat_id=chat.telegram_chat_id) is None

    retry_at = now + timedelta(minutes=5)
    assert await scheduler.run_once(now=retry_at) == 1
    assert bot.send_message.await_count == 2

    async with session_factory() as session:
        repo = SqlAlchemyActivityRepository(session)
        state = await repo.get_chat_interesting_fact_state(chat_id=chat.telegram_chat_id)
        assert state is not None
        assert state.last_sent_at == retry_at
        assert state.last_fact_id is not None
        claims_stmt = select(ChatInterestingFactDeliveryModel).order_by(ChatInterestingFactDeliveryModel.id)
        claims = (await session.execute(claims_stmt)).scalars().all()
        assert [(item.status, item.telegram_message_id, item.error_summary) for item in claims] == [
            ("failed", None, "TelegramBadRequest"),
            ("sent", 777, None),
        ]

    await engine.dispose()


@pytest.mark.asyncio
async def test_interesting_fact_expired_claim_is_abandoned_and_still_cools_chat_down(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    chat = ChatSnapshot(telegram_chat_id=-100903, chat_type="group", title="Facts")
    now = datetime(2026, 3, 19, 12, 0, tzinfo=timezone.utc)
    await _seed_fact_chat(session_factory, chat, now)

    # A worker claimed a slot 10 minutes ago and died before recording the outcome.
    async with session_factory() as session:
        repo = SqlAlchemyActivityRepository(session)
        await repo.create_interesting_fact_claim(
            chat_id=chat.telegram_chat_id,
            fact_id="fact_dead",
            claimed_at=now - timedelta(minutes=10),
            lease_until=now - timedelta(minutes=5),
        )
        await session.commit()

    bot = SimpleNamespace(send_message=AsyncMock(return_value=SimpleNamespace(message_id=1)))
    scheduler = InterestingFactsScheduler(
        bot=bot,
        session_factory=session_factory,
        catalog=_write_facts(tmp_path, ["Тестовый факт"]),
    )
    monkeypatch.setattr(
        "selara.presentation.interesting_facts.GAME_STORE.get_active_game_for_chat",
        AsyncMock(return_value=None),
    )

    assert await scheduler.run_once(now=now) == 0
    bot.send_message.assert_not_awaited()

    async with session_factory() as session:
        claims = (await session.execute(select(ChatInterestingFactDeliveryModel))).scalars().all()
        assert [(item.fact_id, item.status, item.error_summary) for item in claims] == [
            ("fact_dead", "abandoned", "lease_expired"),
        ]

    await engine.dispose()


@pytest.mark.asyncio
async def test_interesting_fact_network_error_is_not_retried_while_outcome_is_unknown(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    chat = ChatSnapshot(telegram_chat_id=-100904, chat_type="group", title="Facts")
    now = datetime(2026, 3, 19, 12, 0, tzinfo=timezone.utc)
    await _seed_fact_chat(session_factory, chat, now)

    # A timeout may still have delivered the message, so the slot must not be sent again.
    timeout = TelegramNetworkError(method=SendMessage(chat_id=chat.telegram_chat_id, text="x"), message="timeout")
    bot = SimpleNamespace(send_message=AsyncMock(side_effect=[timeout, SimpleNamespace(message_id=1)]))
    scheduler = InterestingFactsScheduler(
        bot=bot,
        session_factory=session_factory,
        catalog=_write_facts(tmp_path, ["Тестовый факт"]),
    )
    monkeypatch.setattr(
        "selara.presentation.interesting_facts.GAME_STORE.get_active_game_for_chat",
        AsyncMock(return_value=None),
    )

    assert await scheduler.run_once(now=now) == 0
    async with session_factory() as session:
        claims_stmt = select(ChatInterestingFactDeliveryModel)
        claims = (await session.execute(claims_stmt)).scalars().all()
        assert [(item.status, item.finished_at) for item in claims] == [("claimed", None)]

    assert await scheduler.run_once(now=now + timedelta(minutes=5)) == 0
    bot.send_message.assert_awaited_once()

    await engine.dispose()


@pytest.mark.asyncio
async def test_interesting_fact_stale_claim_outcome_does_not_overwrite_newer_state(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    chat = ChatSnapshot(telegram_chat_id=-100905, chat_type="group", title="Facts")
    now = datetime(2026, 3, 19, 12, 0, tzinfo=timezone.utc)
    await _seed_fact_chat(session_factory, chat, now)

    scheduler = InterestingFactsScheduler(
        bot=SimpleNamespace(send_message=AsyncMock()),
        session_factory=session_factory,
        catalog=_write_facts(tmp_path, ["Тестовый факт", "Другой факт"]),
    )
    monkeypatch.setattr(
        "selara.presentation.interesting_facts.GAME_STORE.get_active_game_for_chat",
        AsyncMock(return_value=None),
    )

    # Worker A claims a slot and then stalls inside Telegram past the lease.
    stale_claim = await scheduler._claim_next_fact(
        chat=chat,
        facts=scheduler.get_facts(),
        now=now,
        has_active_game=False,
    )
    assert stale_claim is not None

    # Worker B abandons the expired claim and records a newer successful delivery.
    newer_at = now + timedelta(hours=4)
    async with session_factory() as session:
        repo = SqlAlchemyActivityRepository(session)
        await repo.abandon_expired_interesting_fact_claims(chat_id=chat.telegram_chat_id, now=newer_at)
        await repo.upsert_chat_interesting_fact_state(
            chat=chat,
            last_sent_at=newer_at,
            last_fact_id="fact_newer",
            used_fact_ids=["fact_newer"],
        )
        await session.commit()

    # Worker A's Telegram call finally returns. Its late outcome must not roll the state back.
    await scheduler._finish_claim(stale_claim, status="sent", telegram_message_id=501)

    async with session_factory() as session:
        repo = SqlAlchemyActivityRepository(session)
        state = await repo.get_chat_interesting_fact_state(chat_id=chat.telegram_chat_id)
        assert state is not None
        assert state.last_sent_at == newer_at
        assert state.last_fact_id == "fact_newer"
        assert state.used_fact_ids == ("fact_newer",)
        claims = (await session.execute(select(ChatInterestingFactDeliveryModel))).scalars().all()
        assert [(item.id, item.status, item.telegram_message_id) for item in claims] == [
            (stale_claim.claim_id, "abandoned", None),
        ]

    await engine.dispose()


@pytest.mark.asyncio
async def test_interesting_fact_second_claim_waits_while_first_claim_is_in_flight(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    chat = ChatSnapshot(telegram_chat_id=-100906, chat_type="group", title="Facts")
    human = UserSnapshot(telegram_user_id=1, username="human", first_name="Human", last_name=None, is_bot=False)
    now = datetime(2026, 3, 19, 12, 0, tzinfo=timezone.utc)
    short_interval = replace(_fact_settings(), interesting_facts_interval_minutes=1)
    async with session_factory() as session:
        repo = SqlAlchemyActivityRepository(session)
        await repo.upsert_chat_settings(chat=chat, values=settings_to_dict(short_interval))
        await repo.upsert_activity(chat=chat, user=human, event_at=now - timedelta(hours=5))
        await session.commit()

    scheduler = InterestingFactsScheduler(
        bot=SimpleNamespace(send_message=AsyncMock()),
        session_factory=session_factory,
        catalog=_write_facts(tmp_path, ["Тестовый факт", "Другой факт"]),
    )
    monkeypatch.setattr(
        "selara.presentation.interesting_facts.GAME_STORE.get_active_game_for_chat",
        AsyncMock(return_value=None),
    )

    first = await scheduler._claim_next_fact(
        chat=chat,
        facts=scheduler.get_facts(),
        now=now,
        has_active_game=False,
    )
    assert first is not None

    # The interval has elapsed, but the first claim is still in flight within its 5 minute lease.
    second = await scheduler._claim_next_fact(
        chat=chat,
        facts=scheduler.get_facts(),
        now=now + timedelta(minutes=2),
        has_active_game=False,
    )
    assert second is None

    async with session_factory() as session:
        claims = (await session.execute(select(ChatInterestingFactDeliveryModel))).scalars().all()
        assert [(item.id, item.status) for item in claims] == [(first.claim_id, "claimed")]

    await engine.dispose()
