from __future__ import annotations

from dataclasses import replace

from selara.core.chat_settings import ChatSettings
from selara.presentation.handlers.settings_common import apply_setting_update, settings_to_dict


def _chat_settings() -> ChatSettings:
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
    )


def test_apply_setting_update_rejects_top_default_greater_than_top_max() -> None:
    current = settings_to_dict(_chat_settings())
    defaults = settings_to_dict(_chat_settings())
    updated, error = apply_setting_update(
        key="top_limit_default",
        raw_value="100",
        current=current,
        defaults=defaults,
    )
    assert updated is None
    assert error == "top_limit_default не может быть больше top_limit_max"


def test_apply_setting_update_rejects_invalid_weight_sum() -> None:
    base = _chat_settings()
    current = settings_to_dict(replace(base, leaderboard_hybrid_karma_weight=0.7, leaderboard_hybrid_activity_weight=0.3))
    defaults = settings_to_dict(base)
    updated, error = apply_setting_update(
        key="leaderboard_hybrid_karma_weight",
        raw_value="0.9",
        current=current,
        defaults=defaults,
    )
    assert updated is None
    assert error == "Сумма весов leaderboard_hybrid_karma_weight и leaderboard_hybrid_activity_weight должна быть 1.0"


def test_apply_setting_update_accepts_valid_bool_change() -> None:
    current = settings_to_dict(_chat_settings())
    defaults = settings_to_dict(_chat_settings())
    updated, error = apply_setting_update(
        key="leaderboard_hybrid_buttons_enabled",
        raw_value="true",
        current=current,
        defaults=defaults,
    )
    assert error is None
    assert updated is not None
    assert updated["leaderboard_hybrid_buttons_enabled"] is True


def test_apply_setting_update_rejects_week_start_hour_out_of_range() -> None:
    current = settings_to_dict(_chat_settings())
    defaults = settings_to_dict(_chat_settings())
    updated, error = apply_setting_update(
        key="leaderboard_week_start_hour",
        raw_value="24",
        current=current,
        defaults=defaults,
    )
    assert updated is None
    assert error == "Значение должно быть в диапазоне 0..23"


def test_apply_setting_update_accepts_antiraid_window_from_allowed_values() -> None:
    current = settings_to_dict(_chat_settings())
    defaults = settings_to_dict(_chat_settings())
    updated, error = apply_setting_update(
        key="antiraid_recent_window_minutes",
        raw_value="5",
        current=current,
        defaults=defaults,
    )

    assert error is None
    assert updated is not None
    assert updated["antiraid_recent_window_minutes"] == 5


def test_apply_setting_update_rejects_antiraid_window_outside_allowed_values() -> None:
    current = settings_to_dict(_chat_settings())
    defaults = settings_to_dict(_chat_settings())
    updated, error = apply_setting_update(
        key="antiraid_recent_window_minutes",
        raw_value="7",
        current=current,
        defaults=defaults,
    )

    assert updated is None
    assert error == "antiraid_recent_window_minutes должен быть равен 5 или 10"
