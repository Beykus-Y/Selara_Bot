from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from selara.core.chat_settings import ChatSettings
from selara.domain.entities import ChatAuditLogEntry
from selara.presentation.handlers import chat_assistant


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


def _moderator_message() -> SimpleNamespace:
    return SimpleNamespace(
        chat=SimpleNamespace(id=-100123, type="group", title="Test chat"),
        from_user=SimpleNamespace(id=1, username="actor", first_name="Actor", last_name=None, is_bot=False),
        answer=AsyncMock(),
    )


def _join_message(member: SimpleNamespace) -> SimpleNamespace:
    return SimpleNamespace(
        chat=SimpleNamespace(id=-100123, type="group", title="Test chat"),
        message_id=77,
        date=datetime(2026, 3, 19, 12, 0, tzinfo=timezone.utc),
        new_chat_members=[member],
    )


@pytest.mark.asyncio
async def test_manage_chat_gate_command_enables_antiraid_locks_chat_and_runs_sweep(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    message = _moderator_message()
    activity_repo = SimpleNamespace(
        upsert_chat_settings=AsyncMock(
            return_value=replace(_BASE_CHAT_SETTINGS, antiraid_enabled=True, antiraid_recent_window_minutes=5)
        )
    )
    save_baseline_mock = AsyncMock(return_value=None)
    lock_mock = AsyncMock(return_value=True)
    retro_ban_mock = AsyncMock(return_value=3)

    monkeypatch.setattr(chat_assistant, "_require_moderate_users", AsyncMock(return_value=True))
    monkeypatch.setattr(chat_assistant, "_save_chat_permissions_baseline", save_baseline_mock)
    monkeypatch.setattr(chat_assistant, "_lock_chat", lock_mock)
    monkeypatch.setattr(chat_assistant, "_ban_recent_joiners", retro_ban_mock)
    monkeypatch.setattr(chat_assistant, "log_chat_action", AsyncMock())

    await chat_assistant.manage_chat_gate_command(
        message,
        activity_repo=activity_repo,
        bot=SimpleNamespace(),
        chat_settings=_BASE_CHAT_SETTINGS,
        command_key="antiraid_on",
        raw_args="5",
    )

    activity_repo.upsert_chat_settings.assert_awaited_once()
    values = activity_repo.upsert_chat_settings.await_args.kwargs["values"]
    assert values["antiraid_enabled"] is True
    assert values["antiraid_recent_window_minutes"] == 5
    save_baseline_mock.assert_awaited_once()
    lock_mock.assert_awaited_once()
    retro_ban_mock.assert_awaited_once()
    assert "Retro-ban: 3" in message.answer.await_args.args[0]


@pytest.mark.asyncio
async def test_manage_chat_gate_command_antiraid_off_keeps_manual_lock(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    message = _moderator_message()
    current_settings = replace(_BASE_CHAT_SETTINGS, antiraid_enabled=True, chat_write_locked=True)
    updated_settings = replace(_BASE_CHAT_SETTINGS, antiraid_enabled=False, chat_write_locked=True)
    activity_repo = SimpleNamespace(upsert_chat_settings=AsyncMock(return_value=updated_settings))
    restore_mock = AsyncMock(return_value=True)

    monkeypatch.setattr(chat_assistant, "_require_moderate_users", AsyncMock(return_value=True))
    monkeypatch.setattr(chat_assistant, "_restore_chat_permissions", restore_mock)
    monkeypatch.setattr(chat_assistant, "log_chat_action", AsyncMock())

    await chat_assistant.manage_chat_gate_command(
        message,
        activity_repo=activity_repo,
        bot=SimpleNamespace(),
        chat_settings=current_settings,
        command_key="antiraid_off",
    )

    restore_mock.assert_not_awaited()
    assert "остаётся закрытым" in message.answer.await_args.args[0]


@pytest.mark.asyncio
async def test_manage_chat_gate_command_chat_unlock_respects_active_antiraid(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    message = _moderator_message()
    current_settings = replace(_BASE_CHAT_SETTINGS, antiraid_enabled=True, chat_write_locked=True)
    updated_settings = replace(_BASE_CHAT_SETTINGS, antiraid_enabled=True, chat_write_locked=False)
    activity_repo = SimpleNamespace(upsert_chat_settings=AsyncMock(return_value=updated_settings))
    restore_mock = AsyncMock(return_value=True)

    monkeypatch.setattr(chat_assistant, "_require_moderate_users", AsyncMock(return_value=True))
    monkeypatch.setattr(chat_assistant, "_restore_chat_permissions", restore_mock)
    monkeypatch.setattr(chat_assistant, "log_chat_action", AsyncMock())

    await chat_assistant.manage_chat_gate_command(
        message,
        activity_repo=activity_repo,
        bot=SimpleNamespace(),
        chat_settings=current_settings,
        command_key="chat_unlock",
    )

    restore_mock.assert_not_awaited()
    assert "остаётся закрытым" in message.answer.await_args.args[0]


@pytest.mark.asyncio
async def test_restore_chat_permissions_uses_saved_baseline() -> None:
    baseline_entry = ChatAuditLogEntry(
        id=1,
        chat_id=-100123,
        actor_user_id=1,
        target_user_id=None,
        action_code="chat_permissions_baseline",
        description="baseline",
        meta_json={
            "permissions": {
                "can_send_messages": True,
                "can_send_other_messages": False,
                "can_invite_users": False,
            },
            "use_independent_chat_permissions": False,
        },
        created_at=datetime(2026, 3, 19, 11, 55, tzinfo=timezone.utc),
    )
    activity_repo = SimpleNamespace(list_audit_logs_by_action=AsyncMock(return_value=[baseline_entry]))
    bot = SimpleNamespace(set_chat_permissions=AsyncMock())

    restored = await chat_assistant._restore_chat_permissions(bot, activity_repo, chat_id=-100123)

    assert restored is True
    bot.set_chat_permissions.assert_awaited_once()
    permissions = bot.set_chat_permissions.await_args.kwargs["permissions"]
    assert permissions.can_send_messages is True
    assert permissions.can_invite_users is False
    assert bot.set_chat_permissions.await_args.kwargs["use_independent_chat_permissions"] is False


@pytest.mark.asyncio
async def test_new_chat_members_handler_bans_joiner_during_antiraid() -> None:
    member = SimpleNamespace(
        id=55,
        username="raider",
        first_name="Raid",
        last_name=None,
        is_bot=False,
    )
    message = _join_message(member)
    bot = SimpleNamespace(
        get_chat_member=AsyncMock(return_value=SimpleNamespace(status="member", user=member)),
        ban_chat_member=AsyncMock(),
        send_message=AsyncMock(),
    )
    activity_repo = SimpleNamespace(
        set_chat_member_active=AsyncMock(),
        get_chat_display_name=AsyncMock(return_value=None),
        add_audit_log=AsyncMock(),
    )
    achievement_orchestrator = SimpleNamespace(process_membership=AsyncMock())

    await chat_assistant.new_chat_members_handler(
        message,
        bot=bot,
        activity_repo=activity_repo,
        achievement_orchestrator=achievement_orchestrator,
        chat_settings=replace(
            _BASE_CHAT_SETTINGS,
            antiraid_enabled=True,
            entry_captcha_enabled=True,
            welcome_enabled=True,
            welcome_cleanup_service_messages=False,
        ),
    )

    bot.ban_chat_member.assert_awaited_once_with(chat_id=message.chat.id, user_id=member.id)
    assert bot.send_message.await_count == 0
    assert activity_repo.set_chat_member_active.await_count == 2
    action_codes = [call.kwargs["action_code"] for call in activity_repo.add_audit_log.await_args_list]
    assert "member_joined" in action_codes
    assert "antiraid_join_ban" in action_codes
