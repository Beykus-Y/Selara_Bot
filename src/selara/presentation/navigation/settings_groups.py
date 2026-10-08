"""Thematic categories for the group settings opened from private chat.

Every key in CHAT_SETTINGS_KEYS belongs to exactly one category. The unit test
checks that, so a new setting cannot silently vanish from the grouped screen.
Titles, editors and validators stay in settings_common; this module only
groups the keys and explains what each group affects.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class SettingsCategory:
    slug: str
    title: str
    summary: str
    settings: tuple[str, ...]


SETTINGS_CATEGORIES: tuple[SettingsCategory, ...] = (
    SettingsCategory(
        slug="basics",
        title="🧭 Основное",
        summary="Лимиты топов и голосований, рейтинги, текстовые команды и сохранение сообщений.",
        settings=(
            "top_limit_default",
            "top_limit_max",
            "vote_daily_limit",
            "leaderboard_hybrid_buttons_enabled",
            "leaderboard_hybrid_karma_weight",
            "leaderboard_hybrid_activity_weight",
            "leaderboard_7d_days",
            "leaderboard_week_start_weekday",
            "leaderboard_week_start_hour",
            "text_commands_enabled",
            "text_commands_locale",
            "iris_view",
            "actions_18_enabled",
            "smart_triggers_enabled",
            "save_message",
        ),
    ),
    SettingsCategory(
        slug="games",
        title="🎲 Игры и экономика",
        summary="Таймеры мафии, аукционы, титулы, крафт и всё, что касается валюты группы.",
        settings=(
            "mafia_night_seconds",
            "mafia_day_seconds",
            "mafia_vote_seconds",
            "mafia_reveal_eliminated_role",
            "craft_enabled",
            "auctions_enabled",
            "auction_duration_minutes",
            "auction_min_increment",
            "titles_enabled",
            "title_price",
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
        ),
    ),
    SettingsCategory(
        slug="social",
        title="💬 Общение и питомцы",
        summary="Персона, семейное древо, RP-действия, интересные факты и питомцы.",
        settings=(
            "persona_enabled",
            "persona_display_mode",
            "family_tree_enabled",
            "custom_rp_enabled",
            "interesting_facts_enabled",
            "interesting_facts_interval_minutes",
            "interesting_facts_target_messages",
            "interesting_facts_sleep_cap_minutes",
            "pets_enabled",
            "pets_spontaneous_enabled",
        ),
    ),
    SettingsCategory(
        slug="ai",
        title="🤖 AI и итоги дня",
        summary="Ответы ИИ в группе, распознавание голосовых и ежедневные итоги дня.",
        settings=(
            "llm_enabled",
            "llm_context_threshold",
            "instant_stt_enabled",
            "daily_summary_enabled",
            "daily_summary_hour",
            "daily_summary_min_messages",
            "daily_summary_style",
            "daily_summary_include_voice",
            "daily_summary_include_video_notes",
        ),
    ),
    SettingsCategory(
        slug="greetings",
        title="👋 Приветствия и выход",
        summary="Текст и кнопка приветствия, прощание и очистка служебных сообщений.",
        settings=(
            "welcome_enabled",
            "welcome_text",
            "welcome_button_text",
            "welcome_button_url",
            "goodbye_enabled",
            "goodbye_text",
            "welcome_cleanup_service_messages",
            "cleanup_leave_service_messages",
        ),
    ),
    SettingsCategory(
        slug="moderation",
        title="🛡 Модерация",
        summary="Капча для новых участников, защита от рейдов и закрытие записи в чат.",
        settings=(
            "entry_captcha_enabled",
            "entry_captcha_timeout_seconds",
            "entry_captcha_kick_on_fail",
            "antiraid_enabled",
            "antiraid_recent_window_minutes",
            "chat_write_locked",
        ),
    ),
)


def category_index_for_key(key: str) -> int:
    for index, category in enumerate(SETTINGS_CATEGORIES):
        if key in category.settings:
            return index
    raise KeyError(f"setting {key!r} is not in any settings category")
