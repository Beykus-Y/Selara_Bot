from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timedelta, timezone
import importlib.util

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from selara.core.chat_settings import ChatSettings
from selara.domain.entities import ChatSnapshot
from selara.infrastructure.db.base import Base
from selara.infrastructure.db.repositories import SqlAlchemyActivityRepository
from selara.presentation.handlers.settings_common import settings_to_dict

pytestmark = pytest.mark.skipif(importlib.util.find_spec("aiosqlite") is None, reason="aiosqlite is not installed")


_BASE_CHAT_SETTINGS = ChatSettings(
    top_limit_default=10,
    top_limit_max=50,
    vote_daily_limit=20,
    leaderboard_hybrid_karma_weight=0.7,
    leaderboard_hybrid_activity_weight=0.3,
    leaderboard_7d_days=7,
    leaderboard_week_start_weekday=0,
    leaderboard_week_start_hour=0,
    mafia_night_seconds=90,
    mafia_day_seconds=120,
    mafia_vote_seconds=60,
    mafia_reveal_eliminated_role=True,
    text_commands_enabled=True,
    text_commands_locale="ru",
    actions_18_enabled=True,
    smart_triggers_enabled=True,
    welcome_enabled=True,
    welcome_text="Привет, {user}! Добро пожаловать в {chat}.",
    welcome_button_text="",
    welcome_button_url="",
    goodbye_enabled=False,
    goodbye_text="Пока, {user}.",
    welcome_cleanup_service_messages=True,
    entry_captcha_enabled=False,
    entry_captcha_timeout_seconds=180,
    entry_captcha_kick_on_fail=True,
    antiraid_enabled=False,
    antiraid_recent_window_minutes=10,
    chat_write_locked=False,
    custom_rp_enabled=True,
    family_tree_enabled=True,
    titles_enabled=True,
    title_price=50000,
    craft_enabled=True,
    auctions_enabled=True,
    auction_duration_minutes=10,
    auction_min_increment=100,
    economy_enabled=True,
    economy_mode="global",
    economy_tap_cooldown_seconds=45,
    economy_daily_base_reward=120,
    economy_daily_streak_cap=7,
    economy_lottery_ticket_price=150,
    economy_lottery_paid_daily_limit=10,
    economy_transfer_daily_limit=5000,
    economy_transfer_tax_percent=5,
    economy_market_fee_percent=2,
    economy_negative_event_chance_percent=22,
    economy_negative_event_loss_percent=30,
)


@pytest.mark.asyncio
async def test_chat_settings_roundtrip_includes_chat_gate_fields() -> None:
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    session_factory = async_sessionmaker(engine, expire_on_commit=False)

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    chat = ChatSnapshot(telegram_chat_id=-100123, chat_type="group", title="Settings")

    async with session_factory() as session:
        repo = SqlAlchemyActivityRepository(session)
        stored = replace(_BASE_CHAT_SETTINGS, antiraid_enabled=True, antiraid_recent_window_minutes=5, chat_write_locked=True)

        await repo.upsert_chat_settings(chat=chat, values=settings_to_dict(stored))
        loaded = await repo.get_chat_settings(chat_id=chat.telegram_chat_id)

        assert loaded is not None
        assert loaded.antiraid_enabled is True
        assert loaded.antiraid_recent_window_minutes == 5
        assert loaded.chat_write_locked is True

    await engine.dispose()


@pytest.mark.asyncio
async def test_audit_log_created_at_override_supports_recent_join_lookup() -> None:
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    session_factory = async_sessionmaker(engine, expire_on_commit=False)

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    chat = ChatSnapshot(telegram_chat_id=-100456, chat_type="group", title="Audit")
    older_join = datetime(2026, 3, 19, 11, 45, tzinfo=timezone.utc)
    recent_join = older_join + timedelta(minutes=9)

    async with session_factory() as session:
        repo = SqlAlchemyActivityRepository(session)

        await repo.add_audit_log(
            chat=chat,
            action_code="member_joined",
            description="old join",
            target_user_id=10,
            created_at=older_join,
        )
        await repo.add_audit_log(
            chat=chat,
            action_code="member_joined",
            description="recent join",
            target_user_id=20,
            created_at=recent_join,
        )
        await repo.add_audit_log(
            chat=chat,
            action_code="chat_locked",
            description="ignore",
            actor_user_id=1,
            created_at=recent_join,
        )

        rows = await repo.list_audit_logs_by_action(
            chat_id=chat.telegram_chat_id,
            action_code="member_joined",
            since=recent_join - timedelta(minutes=1),
            limit=10,
        )

        assert [row.target_user_id for row in rows] == [20]
        assert rows[0].created_at == recent_join

    await engine.dispose()
