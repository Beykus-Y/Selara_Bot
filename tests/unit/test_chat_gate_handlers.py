from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timezone
import logging
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from aiogram.types import Chat, Message, User
from sqlalchemy.exc import SQLAlchemyError

from selara.core.chat_settings import ChatSettings
from selara.domain.entities import ChatAuditLogEntry
from selara.presentation.handlers import chat_assistant
from selara.presentation.middlewares import chat_settings as chat_settings_middleware


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


def _leave_message(
    *,
    first_name: str = "Left",
    last_name: str | None = "User",
    username: str | None = "left_user",
) -> SimpleNamespace:
    return SimpleNamespace(
        chat=SimpleNamespace(id=-100123, type="group", title="Test chat"),
        message_id=78,
        date=datetime(2026, 7, 14, 18, 52, tzinfo=timezone.utc),
        left_chat_member=SimpleNamespace(
            id=501,
            username=username,
            first_name=first_name,
            last_name=last_name,
            is_bot=False,
        ),
    )


def _leave_dependencies(*, notification_message_id: int = 901) -> tuple[SimpleNamespace, SimpleNamespace, SimpleNamespace]:
    bot = SimpleNamespace(
        delete_message=AsyncMock(),
        send_message=AsyncMock(return_value=SimpleNamespace(message_id=notification_message_id)),
    )
    activity_repo = SimpleNamespace(
        set_chat_member_active=AsyncMock(),
        get_chat_display_name=AsyncMock(return_value=None),
        add_audit_log=AsyncMock(),
    )
    achievement_orchestrator = SimpleNamespace(process_membership=AsyncMock())
    return bot, activity_repo, achievement_orchestrator


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


@pytest.mark.asyncio
async def test_left_chat_member_handler_deletes_service_message_and_sends_goodbye() -> None:
    message = _leave_message()
    bot, activity_repo, achievement_orchestrator = _leave_dependencies(notification_message_id=902)

    await chat_assistant.left_chat_member_handler(
        message,
        bot=bot,
        activity_repo=activity_repo,
        achievement_orchestrator=achievement_orchestrator,
        chat_settings=replace(
            _BASE_CHAT_SETTINGS,
            goodbye_enabled=True,
            cleanup_leave_service_messages=True,
        ),
        settings_source="database",
        event_update=SimpleNamespace(update_id=122602390),
    )

    bot.delete_message.assert_awaited_once_with(chat_id=message.chat.id, message_id=message.message_id)
    bot.send_message.assert_awaited_once()
    assert bot.send_message.await_args.kwargs["parse_mode"] == "HTML"
    action_calls = {call.kwargs["action_code"]: call.kwargs for call in activity_repo.add_audit_log.await_args_list}
    assert action_calls["service_leave_deleted"]["meta_json"] == {
        "user_id": message.left_chat_member.id,
        "service_message_id": message.message_id,
        "update_id": 122602390,
        "settings_source": "database",
    }
    assert action_calls["leave_notification_sent"]["meta_json"]["notification_message_id"] == 902


@pytest.mark.asyncio
async def test_left_chat_member_handler_skips_goodbye_disabled_from_database(caplog: pytest.LogCaptureFixture) -> None:
    message = _leave_message()
    bot, activity_repo, achievement_orchestrator = _leave_dependencies()

    with caplog.at_level(logging.INFO, logger=chat_assistant.__name__):
        await chat_assistant.left_chat_member_handler(
            message,
            bot=bot,
            activity_repo=activity_repo,
            achievement_orchestrator=achievement_orchestrator,
            chat_settings=replace(
                _BASE_CHAT_SETTINGS,
                goodbye_enabled=False,
                cleanup_leave_service_messages=True,
            ),
            settings_source="database",
            event_update=SimpleNamespace(update_id=122603349),
        )

    bot.delete_message.assert_not_awaited()
    bot.send_message.assert_not_awaited()
    assert "leave_notification_skipped" in caplog.text
    assert "settings_source=database" in caplog.text


@pytest.mark.asyncio
async def test_left_chat_member_handler_skips_goodbye_after_settings_db_error(
    caplog: pytest.LogCaptureFixture,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    message = Message(
        message_id=78,
        date=datetime(2026, 7, 14, 21, 7, tzinfo=timezone.utc),
        chat=Chat(id=-100123, type="group", title="Test chat"),
        left_chat_member=User(id=501, is_bot=False, first_name="Left", username=None),
    )
    bot, activity_repo, achievement_orchestrator = _leave_dependencies()
    activity_repo.get_chat_settings = AsyncMock(side_effect=SQLAlchemyError("settings unavailable"))
    db_session = SimpleNamespace(rollback=AsyncMock())
    fallback_settings = replace(
        _BASE_CHAT_SETTINGS,
        goodbye_enabled=False,
        cleanup_leave_service_messages=False,
    )
    monkeypatch.setattr(chat_settings_middleware, "default_chat_settings", lambda settings: fallback_settings)

    async def dispatch(event: Message, data: dict[str, object]) -> None:
        await chat_assistant.left_chat_member_handler(
            event,
            bot=bot,
            activity_repo=activity_repo,
            achievement_orchestrator=achievement_orchestrator,
            chat_settings=data["chat_settings"],
            settings_source=data["settings_source"],
            event_update=data["event_update"],
        )

    with caplog.at_level(logging.INFO):
        await chat_settings_middleware.ChatSettingsMiddleware()(
            dispatch,
            message,
            {
                "settings": object(),
                "activity_repo": activity_repo,
                "db_session": db_session,
                "event_update": SimpleNamespace(update_id=122603350),
            },
        )

    db_session.rollback.assert_awaited_once()
    bot.delete_message.assert_not_awaited()
    bot.send_message.assert_not_awaited()
    assert "chat_settings_load_failed" in caplog.text
    assert "leave_notification_skipped" in caplog.text
    assert "settings_source=default_after_db_error" in caplog.text


@pytest.mark.asyncio
async def test_left_chat_member_handler_logs_and_reraises_send_error(caplog: pytest.LogCaptureFixture) -> None:
    message = _leave_message()
    bot, activity_repo, achievement_orchestrator = _leave_dependencies()
    bot.send_message.side_effect = RuntimeError("send failed")

    with caplog.at_level(logging.ERROR, logger=chat_assistant.__name__):
        with pytest.raises(RuntimeError, match="send failed"):
            await chat_assistant.left_chat_member_handler(
                message,
                bot=bot,
                activity_repo=activity_repo,
                achievement_orchestrator=achievement_orchestrator,
                chat_settings=replace(_BASE_CHAT_SETTINGS, goodbye_enabled=True),
                settings_source="database",
                event_update=SimpleNamespace(update_id=122603349),
            )

    failure_record = next(
        record for record in caplog.records if record.getMessage().startswith("leave_notification_send_failed")
    )
    assert failure_record.exc_info is not None
    action_codes = [call.kwargs["action_code"] for call in activity_repo.add_audit_log.await_args_list]
    assert "leave_notification_sent" not in action_codes
    assert "leave_notification_failed" not in action_codes


@pytest.mark.asyncio
async def test_left_chat_member_handler_escapes_html_in_user_name() -> None:
    message = _leave_message(first_name='Иван <Admin> & "Owner"', last_name=None, username=None)
    bot, activity_repo, achievement_orchestrator = _leave_dependencies()

    await chat_assistant.left_chat_member_handler(
        message,
        bot=bot,
        activity_repo=activity_repo,
        achievement_orchestrator=achievement_orchestrator,
        chat_settings=replace(_BASE_CHAT_SETTINGS, goodbye_enabled=True),
        settings_source="database",
        event_update=SimpleNamespace(update_id=122602390),
    )

    sent_text = bot.send_message.await_args.kwargs["text"]
    assert 'href="tg://user?id=501"' in sent_text
    assert 'Иван &lt;Admin&gt; &amp; &quot;Owner&quot;' in sent_text
    assert 'Иван <Admin> & "Owner"' not in sent_text
    assert bot.send_message.await_args.kwargs["parse_mode"] == "HTML"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("username", "expected_label"),
    [
        ("left_user", "Left User"),
        (None, "Left User"),
    ],
)
async def test_left_chat_member_handler_uses_clickable_profile_mention(
    username: str | None,
    expected_label: str,
) -> None:
    message = _leave_message(username=username)
    bot, activity_repo, achievement_orchestrator = _leave_dependencies()

    await chat_assistant.left_chat_member_handler(
        message,
        bot=bot,
        activity_repo=activity_repo,
        achievement_orchestrator=achievement_orchestrator,
        chat_settings=replace(_BASE_CHAT_SETTINGS, goodbye_enabled=True),
    )

    sent_text = bot.send_message.await_args.kwargs["text"]
    assert f'<a href="tg://user?id={message.left_chat_member.id}">{expected_label}</a>' in sent_text
    assert bot.send_message.await_args.kwargs["parse_mode"] == "HTML"
