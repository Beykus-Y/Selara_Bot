from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from aiogram import F
from aiogram.types import Message

from selara.core.chat_settings import ChatSettings
from selara.domain.entities import ChatRoleDefinition
from selara.presentation.handlers.llm_admin import (
    _execute_rollback,
    _handle,
    llm_admin_context_handler,
    llm_admin_nocontext_handler,
)


def _role(code: str, rank: int, *permissions: str) -> ChatRoleDefinition:
    return ChatRoleDefinition(
        chat_id=-100123,
        role_code=code,
        title_ru=code,
        rank=rank,
        permissions=permissions,
        is_system=True,
        template_key=code,
    )


@pytest.fixture
def chat_settings():
    return ChatSettings(
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
        llm_enabled=True,
    )


def test_ai_prefix_filters():
    # Context filter: F.text.regexp(r"^\?\?")
    # Matches starting with ??
    filter_context = F.chat.type.in_({"group", "supergroup"}) & F.text.regexp(r"^\?\?")
    
    # Non-context filter: F.text.regexp(r"^\?(?!\?)")
    # Matches starting with ? but not ??
    filter_nocontext = F.chat.type.in_({"group", "supergroup"}) & F.text.regexp(r"^\?(?!\?)")

    # Mock messages
    msg_context = SimpleNamespace(chat=SimpleNamespace(type="group"), text="??какой-то вопрос")
    msg_nocontext = SimpleNamespace(chat=SimpleNamespace(type="group"), text="?какой-то вопрос")
    msg_old_context = SimpleNamespace(chat=SimpleNamespace(type="group"), text="!!какой-то вопрос")
    msg_old_nocontext = SimpleNamespace(chat=SimpleNamespace(type="group"), text="!какой-то вопрос")
    msg_other = SimpleNamespace(chat=SimpleNamespace(type="group"), text="привет ??")

    assert filter_context.resolve(msg_context) is not None
    assert filter_context.resolve(msg_nocontext) is None
    assert filter_context.resolve(msg_old_context) is None
    assert filter_context.resolve(msg_old_nocontext) is None
    assert filter_context.resolve(msg_other) is None

    assert filter_nocontext.resolve(msg_context) is None
    assert filter_nocontext.resolve(msg_nocontext) is not None
    assert filter_nocontext.resolve(msg_old_context) is None
    assert filter_nocontext.resolve(msg_old_nocontext) is None
    assert filter_nocontext.resolve(msg_other) is None


@pytest.mark.asyncio
async def test_llm_admin_handlers_dispatch(chat_settings):
    bot = MagicMock()
    activity_repo = MagicMock()
    llm_client = MagicMock()
    db_session = MagicMock()
    message = MagicMock(spec=Message)

    with patch("selara.presentation.handlers.llm_admin._handle", new_callable=AsyncMock) as mock_handle:
        await llm_admin_context_handler(
            message, bot, activity_repo, chat_settings, llm_client, db_session
        )
        mock_handle.assert_awaited_once_with(
            message, bot, activity_repo, chat_settings, llm_client, db_session, with_context=True
        )

    with patch("selara.presentation.handlers.llm_admin._handle", new_callable=AsyncMock) as mock_handle:
        await llm_admin_nocontext_handler(
            message, bot, activity_repo, chat_settings, llm_client, db_session
        )
        mock_handle.assert_awaited_once_with(
            message, bot, activity_repo, chat_settings, llm_client, db_session, with_context=False
        )


@pytest.mark.asyncio
async def test_handle_empty_query(chat_settings):
    bot = MagicMock()
    activity_repo = MagicMock()
    llm_client = MagicMock()
    db_session = MagicMock()
    
    # Mock message
    message = AsyncMock(spec=Message)
    message.chat = SimpleNamespace(id=-100123, type="group", title="Test group")
    message.from_user = SimpleNamespace(
        id=111, username="admin", first_name="Admin", last_name=None, is_bot=False
    )
    message.text = "??"
    message.reply = AsyncMock()

    # We mock permission check to return True
    with patch("selara.presentation.handlers.llm_admin.has_permission", new_callable=AsyncMock) as mock_has_perm:
        mock_has_perm.return_value = (True, None, None)
        
        await _handle(
            message, bot, activity_repo, chat_settings, llm_client, db_session, with_context=True
        )
        
        # Should reply saying to enter request after prefix
        message.reply.assert_awaited_once_with("Введите запрос после ??")


@pytest.mark.asyncio
async def test_rank_rollback_requires_manage_roles_permission() -> None:
    activity_repo = SimpleNamespace(
        _session=SimpleNamespace(
            get=AsyncMock(
                return_value=SimpleNamespace(
                    telegram_user_id=222,
                    username="target",
                    first_name="Target",
                    last_name=None,
                    is_bot=False,
                )
            )
        ),
        get_effective_role_definition=AsyncMock(
            side_effect=[
                _role("custom_moderator", 20, "moderate_users"),
                _role("participant", 0),
            ]
        ),
        get_chat_role_definition=AsyncMock(return_value=_role("junior_admin", 10, "moderate_users")),
        set_bot_role=AsyncMock(),
    )
    actor = SimpleNamespace(id=111, username="actor", first_name="Actor", last_name=None, is_bot=False)

    with pytest.raises(PermissionError, match="рол"):
        await _execute_rollback(
            payload={"tool": "set_rank", "target_user_id": 222, "previous_rank": "junior_admin"},
            chat_id=-100123,
            rollback_by=actor,
            activity_repo=activity_repo,
            bot=MagicMock(),
        )

    activity_repo.set_bot_role.assert_not_awaited()


@pytest.mark.asyncio
async def test_rank_rollback_cannot_restore_role_at_actor_level() -> None:
    activity_repo = SimpleNamespace(
        _session=SimpleNamespace(
            get=AsyncMock(
                return_value=SimpleNamespace(
                    telegram_user_id=222,
                    username="target",
                    first_name="Target",
                    last_name=None,
                    is_bot=False,
                )
            )
        ),
        get_effective_role_definition=AsyncMock(
            side_effect=[
                _role("senior_admin", 20, "manage_roles", "moderate_users"),
                _role("participant", 0),
            ]
        ),
        get_chat_role_definition=AsyncMock(return_value=_role("senior_admin", 20, "manage_roles")),
        set_bot_role=AsyncMock(),
    )
    actor = SimpleNamespace(id=111, username="actor", first_name="Actor", last_name=None, is_bot=False)

    with pytest.raises(PermissionError, match="уров"):
        await _execute_rollback(
            payload={"tool": "set_rank", "target_user_id": 222, "previous_rank": "senior_admin"},
            chat_id=-100123,
            rollback_by=actor,
            activity_repo=activity_repo,
            bot=MagicMock(),
        )

    activity_repo.set_bot_role.assert_not_awaited()
