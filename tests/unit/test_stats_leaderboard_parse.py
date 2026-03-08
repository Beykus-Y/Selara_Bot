from dataclasses import replace

from selara.core.chat_settings import default_chat_settings
from selara.core.config import Settings
from selara.presentation.handlers.stats import (
    parse_activity_top_period_request,
    parse_leaderboard_request,
    should_include_hybrid_top_keyboard,
)


def _chat_settings():
    return default_chat_settings(
        Settings(
            BOT_TOKEN="token",
            DATABASE_URL="sqlite+aiosqlite:///tmp/test.db",
        )
    )


def test_parse_top_karma_with_limit() -> None:
    mode, limit, error = parse_leaderboard_request(
        "карма 17",
        chat_settings=_chat_settings(),
        default_mode="mix",
        allow_mode_switch=True,
    )
    assert error is None
    assert mode == "karma"
    assert limit == 17


def test_parse_active_limit() -> None:
    mode, limit, error = parse_leaderboard_request(
        "12",
        chat_settings=_chat_settings(),
        default_mode="activity",
        allow_mode_switch=False,
    )
    assert error is None
    assert mode == "activity"
    assert limit == 12


def test_parse_active_rejects_mode_switch() -> None:
    mode, limit, error = parse_leaderboard_request(
        "карма 5",
        chat_settings=_chat_settings(),
        default_mode="activity",
        allow_mode_switch=False,
    )
    assert mode is None
    assert limit is None
    assert error is not None


def test_parse_activity_top_period_week_with_default_limit() -> None:
    matched, period, limit, error = parse_activity_top_period_request(
        "неделя",
        chat_settings=_chat_settings(),
    )
    assert matched is True
    assert error is None
    assert period == "week"
    assert limit == _chat_settings().top_limit_default


def test_parse_activity_top_period_rejects_bad_limit() -> None:
    matched, period, limit, error = parse_activity_top_period_request(
        "месяц abc",
        chat_settings=_chat_settings(),
    )
    assert matched is True
    assert period is None
    assert limit is None
    assert error == "Лимит должен быть числом"


def test_hybrid_top_keyboard_is_disabled_by_default() -> None:
    assert (
        should_include_hybrid_top_keyboard(
            chat_settings=_chat_settings(),
            mode="mix",
            period="all",
        )
        is False
    )


def test_hybrid_top_keyboard_works_only_for_mix_all_or_7d() -> None:
    enabled_settings = replace(_chat_settings(), leaderboard_hybrid_buttons_enabled=True)

    assert should_include_hybrid_top_keyboard(chat_settings=enabled_settings, mode="mix", period="all") is True
    assert should_include_hybrid_top_keyboard(chat_settings=enabled_settings, mode="mix", period="7d") is True
    assert should_include_hybrid_top_keyboard(chat_settings=enabled_settings, mode="activity", period="all") is False
    assert should_include_hybrid_top_keyboard(chat_settings=enabled_settings, mode="mix", period="month") is False
