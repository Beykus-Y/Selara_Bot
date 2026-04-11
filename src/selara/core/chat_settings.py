from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any

from selara.core.config import Settings


@dataclass(frozen=True)
class ChatSettings:
    top_limit_default: int
    top_limit_max: int
    vote_daily_limit: int
    leaderboard_hybrid_karma_weight: float
    leaderboard_hybrid_activity_weight: float
    leaderboard_7d_days: int
    leaderboard_week_start_weekday: int
    leaderboard_week_start_hour: int
    mafia_night_seconds: int
    mafia_day_seconds: int
    mafia_vote_seconds: int
    mafia_reveal_eliminated_role: bool
    text_commands_enabled: bool
    text_commands_locale: str
    actions_18_enabled: bool
    smart_triggers_enabled: bool
    welcome_enabled: bool
    welcome_text: str
    welcome_button_text: str
    welcome_button_url: str
    goodbye_enabled: bool
    goodbye_text: str
    welcome_cleanup_service_messages: bool
    entry_captcha_enabled: bool
    entry_captcha_timeout_seconds: int
    entry_captcha_kick_on_fail: bool
    custom_rp_enabled: bool
    family_tree_enabled: bool
    titles_enabled: bool
    title_price: int
    craft_enabled: bool
    auctions_enabled: bool
    auction_duration_minutes: int
    auction_min_increment: int
    economy_enabled: bool
    economy_mode: str
    economy_tap_cooldown_seconds: int
    economy_daily_base_reward: int
    economy_daily_streak_cap: int
    economy_lottery_ticket_price: int
    economy_lottery_paid_daily_limit: int
    economy_transfer_daily_limit: int
    economy_transfer_tax_percent: int
    economy_market_fee_percent: int
    economy_negative_event_chance_percent: int
    economy_negative_event_loss_percent: int
    antiraid_enabled: bool = False
    antiraid_recent_window_minutes: int = 10
    chat_write_locked: bool = False
    cleanup_economy_commands: bool = False
    iris_view: bool = False
    save_message: bool = False
    leaderboard_hybrid_buttons_enabled: bool = False
    interesting_facts_enabled: bool = False
    interesting_facts_interval_minutes: int = 180
    interesting_facts_target_messages: int = 150
    interesting_facts_sleep_cap_minutes: int = 1440
    gacha_enabled: bool = True
    gacha_restore_at: datetime | None = None


CHAT_SETTINGS_KEYS: tuple[str, ...] = (
    "top_limit_default",
    "top_limit_max",
    "vote_daily_limit",
    "leaderboard_hybrid_buttons_enabled",
    "leaderboard_hybrid_karma_weight",
    "leaderboard_hybrid_activity_weight",
    "leaderboard_7d_days",
    "leaderboard_week_start_weekday",
    "leaderboard_week_start_hour",
    "mafia_night_seconds",
    "mafia_day_seconds",
    "mafia_vote_seconds",
    "mafia_reveal_eliminated_role",
    "text_commands_enabled",
    "text_commands_locale",
    "iris_view",
    "actions_18_enabled",
    "smart_triggers_enabled",
    "welcome_enabled",
    "welcome_text",
    "welcome_button_text",
    "welcome_button_url",
    "goodbye_enabled",
    "goodbye_text",
    "welcome_cleanup_service_messages",
    "entry_captcha_enabled",
    "entry_captcha_timeout_seconds",
    "entry_captcha_kick_on_fail",
    "antiraid_enabled",
    "antiraid_recent_window_minutes",
    "chat_write_locked",
    "custom_rp_enabled",
    "family_tree_enabled",
    "save_message",
    "interesting_facts_enabled",
    "interesting_facts_interval_minutes",
    "interesting_facts_target_messages",
    "interesting_facts_sleep_cap_minutes",
    "titles_enabled",
    "title_price",
    "craft_enabled",
    "auctions_enabled",
    "auction_duration_minutes",
    "auction_min_increment",
    "economy_enabled",
    "economy_mode",
    "economy_tap_cooldown_seconds",
    "economy_daily_base_reward",
    "economy_daily_streak_cap",
    "economy_lottery_ticket_price",
    "economy_lottery_paid_daily_limit",
    "economy_transfer_daily_limit",
    "economy_transfer_tax_percent",
    "economy_market_fee_percent",
    "economy_negative_event_chance_percent",
    "economy_negative_event_loss_percent",
    "cleanup_economy_commands",
)


def default_chat_settings(settings: Settings) -> ChatSettings:
    return ChatSettings(
        top_limit_default=settings.top_limit_default,
        top_limit_max=settings.top_limit_max,
        vote_daily_limit=settings.vote_daily_limit,
        leaderboard_hybrid_buttons_enabled=False,
        leaderboard_hybrid_karma_weight=settings.leaderboard_hybrid_karma_weight,
        leaderboard_hybrid_activity_weight=settings.leaderboard_hybrid_activity_weight,
        leaderboard_7d_days=settings.leaderboard_7d_days,
        leaderboard_week_start_weekday=settings.leaderboard_week_start_weekday,
        leaderboard_week_start_hour=settings.leaderboard_week_start_hour,
        mafia_night_seconds=settings.mafia_night_seconds,
        mafia_day_seconds=settings.mafia_day_seconds,
        mafia_vote_seconds=settings.mafia_vote_seconds,
        mafia_reveal_eliminated_role=settings.mafia_reveal_eliminated_role,
        text_commands_enabled=settings.text_commands_enabled,
        text_commands_locale=settings.text_commands_locale,
        actions_18_enabled=settings.actions_18_enabled,
        smart_triggers_enabled=settings.smart_triggers_enabled,
        welcome_enabled=settings.welcome_enabled,
        welcome_text=settings.welcome_text,
        welcome_button_text=settings.welcome_button_text,
        welcome_button_url=settings.welcome_button_url,
        goodbye_enabled=settings.goodbye_enabled,
        goodbye_text=settings.goodbye_text,
        welcome_cleanup_service_messages=settings.welcome_cleanup_service_messages,
        entry_captcha_enabled=settings.entry_captcha_enabled,
        entry_captcha_timeout_seconds=settings.entry_captcha_timeout_seconds,
        entry_captcha_kick_on_fail=settings.entry_captcha_kick_on_fail,
        antiraid_enabled=False,
        antiraid_recent_window_minutes=10,
        chat_write_locked=False,
        custom_rp_enabled=settings.custom_rp_enabled,
        family_tree_enabled=settings.family_tree_enabled,
        titles_enabled=settings.titles_enabled,
        title_price=settings.title_price,
        craft_enabled=settings.craft_enabled,
        auctions_enabled=settings.auctions_enabled,
        auction_duration_minutes=settings.auction_duration_minutes,
        auction_min_increment=settings.auction_min_increment,
        economy_enabled=settings.economy_enabled,
        economy_mode=settings.economy_mode,
        economy_tap_cooldown_seconds=settings.economy_tap_cooldown_seconds,
        economy_daily_base_reward=settings.economy_daily_base_reward,
        economy_daily_streak_cap=settings.economy_daily_streak_cap,
        economy_lottery_ticket_price=settings.economy_lottery_ticket_price,
        economy_lottery_paid_daily_limit=settings.economy_lottery_paid_daily_limit,
        economy_transfer_daily_limit=settings.economy_transfer_daily_limit,
        economy_transfer_tax_percent=settings.economy_transfer_tax_percent,
        economy_market_fee_percent=settings.economy_market_fee_percent,
        economy_negative_event_chance_percent=settings.economy_negative_event_chance_percent,
        economy_negative_event_loss_percent=settings.economy_negative_event_loss_percent,
        cleanup_economy_commands=settings.cleanup_economy_commands,
        save_message=False,
        interesting_facts_enabled=False,
        interesting_facts_interval_minutes=180,
        interesting_facts_target_messages=150,
        interesting_facts_sleep_cap_minutes=1440,
    )


def parse_chat_setting_value(key: str, raw_value: str) -> Any:
    value = raw_value.strip()

    if key in {
        "top_limit_default",
        "top_limit_max",
        "vote_daily_limit",
        "leaderboard_7d_days",
        "mafia_night_seconds",
        "mafia_day_seconds",
        "mafia_vote_seconds",
        "entry_captcha_timeout_seconds",
        "antiraid_recent_window_minutes",
        "title_price",
        "auction_duration_minutes",
        "auction_min_increment",
        "interesting_facts_interval_minutes",
        "interesting_facts_target_messages",
        "interesting_facts_sleep_cap_minutes",
        "economy_tap_cooldown_seconds",
        "economy_daily_base_reward",
        "economy_daily_streak_cap",
        "economy_lottery_ticket_price",
        "economy_lottery_paid_daily_limit",
        "economy_transfer_daily_limit",
    }:
        if not value.isdigit():
            raise ValueError("Значение должно быть целым числом")
        parsed = int(value)
        if parsed <= 0:
            raise ValueError("Значение должно быть > 0")
        return parsed

    if key == "leaderboard_week_start_weekday":
        if not value.isdigit():
            raise ValueError("Значение должно быть целым числом")
        parsed = int(value)
        if not 0 <= parsed <= 6:
            raise ValueError("Значение должно быть в диапазоне 0..6")
        return parsed

    if key == "leaderboard_week_start_hour":
        if not value.isdigit():
            raise ValueError("Значение должно быть целым числом")
        parsed = int(value)
        if not 0 <= parsed <= 23:
            raise ValueError("Значение должно быть в диапазоне 0..23")
        return parsed

    if key in {"leaderboard_hybrid_karma_weight", "leaderboard_hybrid_activity_weight"}:
        try:
            parsed = float(value)
        except ValueError as exc:
            raise ValueError("Значение должно быть числом") from exc
        if not 0 <= parsed <= 1:
            raise ValueError("Значение должно быть в диапазоне 0..1")
        return parsed

    if key in {
        "text_commands_enabled",
        "leaderboard_hybrid_buttons_enabled",
        "mafia_reveal_eliminated_role",
        "iris_view",
        "actions_18_enabled",
        "smart_triggers_enabled",
        "welcome_enabled",
        "goodbye_enabled",
        "welcome_cleanup_service_messages",
        "entry_captcha_enabled",
        "entry_captcha_kick_on_fail",
        "antiraid_enabled",
        "chat_write_locked",
        "custom_rp_enabled",
        "family_tree_enabled",
        "save_message",
        "interesting_facts_enabled",
        "titles_enabled",
        "craft_enabled",
        "auctions_enabled",
        "cleanup_economy_commands",
    }:
        lowered = value.lower()
        if lowered in {"true", "1", "yes", "on"}:
            return True
        if lowered in {"false", "0", "no", "off"}:
            return False
        raise ValueError("Значение должно быть true/false")

    if key == "economy_enabled":
        lowered = value.lower()
        if lowered in {"true", "1", "yes", "on"}:
            return True
        if lowered in {"false", "0", "no", "off"}:
            return False
        raise ValueError("Значение должно быть true/false")

    if key == "text_commands_locale":
        lowered = value.lower()
        if lowered not in {"ru", "en"}:
            raise ValueError("Поддерживаются только locale: ru, en")
        return lowered

    if key in {
        "welcome_text",
        "welcome_button_text",
        "welcome_button_url",
        "goodbye_text",
    }:
        normalized = value.strip()
        if len(normalized) > 1000:
            raise ValueError("Строка слишком длинная")
        return normalized

    if key == "economy_mode":
        lowered = value.lower()
        if lowered not in {"global", "local"}:
            raise ValueError("Поддерживаются только режимы экономики: global, local")
        return lowered

    if key in {
        "economy_transfer_tax_percent",
        "economy_market_fee_percent",
        "economy_negative_event_chance_percent",
        "economy_negative_event_loss_percent",
    }:
        if not value.isdigit():
            raise ValueError("Значение должно быть целым числом")
        parsed = int(value)
        if not 0 <= parsed <= 100:
            raise ValueError("Значение должно быть в диапазоне 0..100")
        return parsed

    raise ValueError("Неизвестный ключ")
