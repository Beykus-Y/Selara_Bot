from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from aiogram.exceptions import TelegramBadRequest

from selara.core.config import Settings
from selara.infrastructure.llm.features import AiFeature
from selara.presentation.commands.command_catalog import GAME_RULES_RU
from selara.presentation.game_state import GAME_LAUNCHABLE_KINDS
from selara.presentation.handlers.help import (
    _HELP_GAMES_ORDER,
    _HELP_SECTIONS_ORDER,
    _build_help_keyboard,
    _parse_help_callback_data,
    _policy_limit,
    _resolve_help_payload,
    help_callback,
)


def _settings() -> Settings:
    return Settings(
        BOT_TOKEN="token",
        DATABASE_URL="sqlite+aiosqlite:///tmp/test.db",
    )


def test_help_home_payload_contains_navigation() -> None:
    text, keyboard = _resolve_help_payload(_settings(), section=None)
    assert "Выберите раздел" in text
    assert keyboard.inline_keyboard


def test_help_section_payload_contains_section_title() -> None:
    text, keyboard = _resolve_help_payload(_settings(), section="economy")
    assert "Экономика" in text
    assert keyboard.inline_keyboard


def test_help_keyboard_home_button_exists_for_section() -> None:
    keyboard = _build_help_keyboard(section="games")
    callbacks = [button.callback_data for row in keyboard.inline_keyboard for button in row]
    assert "help:home" in callbacks


def test_help_unknown_section_falls_back_to_main_text() -> None:
    text, keyboard = _resolve_help_payload(_settings(), section="unknown")
    assert "Выберите раздел" in text
    assert keyboard.inline_keyboard


def test_help_games_section_shows_game_picker() -> None:
    text, keyboard = _resolve_help_payload(_settings(), section="games")
    callbacks = [button.callback_data for row in keyboard.inline_keyboard for button in row]
    assert "Выберите конкретную игру" in text
    assert "help:game_mafia" in callbacks
    assert "help:game_spy" in callbacks
    assert "help:game_bunker" in callbacks


def test_help_game_payload_contains_rules() -> None:
    text, keyboard = _resolve_help_payload(_settings(), section="game_quiz")
    callbacks = [button.callback_data for row in keyboard.inline_keyboard for button in row]
    assert "Викторина" in text
    assert "Правила" in text
    assert "help:games" in callbacks
    assert "help:home" in callbacks


def test_help_callback_parser_reads_plain_section() -> None:
    assert _parse_help_callback_data("help:game_mafia") == "game_mafia"
    assert _parse_help_callback_data("help:economy") == "economy"


def test_help_callback_parser_ignores_legacy_owner_suffix() -> None:
    # Buttons sent before help became public still carry `:u<owner_id>`;
    # they must resolve to the same section for every user.
    assert _parse_help_callback_data("help:game_mafia:u123") == "game_mafia"
    assert _parse_help_callback_data("help:economy:u5") == "economy"


def test_help_callback_parser_falls_back_to_home() -> None:
    assert _parse_help_callback_data(None) == "home"
    assert _parse_help_callback_data("pm:home") == "home"
    assert _parse_help_callback_data("help:") == "home"
    assert _parse_help_callback_data("help::u5") == "home"


def test_help_games_menu_covers_every_launchable_game_kind() -> None:
    # Regression guard: whoami and zlobcards were both real, launchable game
    # modes with zero way to reach their rules from /help — missing from
    # _HELP_GAMES_ORDER entirely, so the in-Telegram games picker silently
    # never offered them.
    menu_keys = {key for key, _title in _HELP_GAMES_ORDER}
    for kind in GAME_LAUNCHABLE_KINDS:
        assert kind in menu_keys, f"{kind}: launchable but missing from the /help games menu"
        text, _keyboard = _resolve_help_payload(Settings(BOT_TOKEN="token", DATABASE_URL="sqlite+aiosqlite:///tmp/test.db"), section=f"game_{kind}")
        assert "Правила" in text, f"{kind}: /help game detail text has no rules"


def test_help_games_section_keyboard_has_a_button_per_launchable_kind() -> None:
    keyboard = _build_help_keyboard(section="games")
    callbacks = {button.callback_data for row in keyboard.inline_keyboard for button in row}
    for kind in GAME_LAUNCHABLE_KINDS:
        assert f"help:game_{kind}" in callbacks


def test_every_help_section_renders_non_empty_command_text() -> None:
    # Sub-slice 6b: section bodies are now built from command_catalog.py
    # syntax at import time instead of hand-typed literals. This is the
    # basic sanity net for that construction — every section still resolves
    # to real, non-broken text with at least one <code> command reference.
    for key, _title in _HELP_SECTIONS_ORDER:
        text, keyboard = _resolve_help_payload(Settings(BOT_TOKEN="token", DATABASE_URL="sqlite+aiosqlite:///tmp/test.db"), section=key)
        assert "<code>" in text, f"{key}: no command reference rendered"
        assert keyboard.inline_keyboard


def test_help_ai_sections_are_reachable_and_fit_a_telegram_message() -> None:
    keys = {key for key, _title in _HELP_SECTIONS_ORDER}
    assert {"ai", "ai_plus", "models"} <= keys
    for key in ("ai", "ai_plus", "models"):
        text, keyboard = _resolve_help_payload(_settings(), section=key)
        callbacks = [button.callback_data for row in keyboard.inline_keyboard for button in row]
        assert f"help:{key}" in callbacks
        assert "help:home" in callbacks
        assert len(text) < 4096


def test_help_ai_section_documents_assistant_call_names_and_limits() -> None:
    text, _ = _resolve_help_payload(_settings(), section="ai")
    for fragment in ("? вопрос", "?? вопрос", "?reset", "/selara кличка", "llm_enabled", f"{_policy_limit(AiFeature.LLM_ADMIN, 'telegram_message')} запросов в сутки"):
        assert fragment in text, fragment
    settings = _settings()
    assert f"{settings.group_member_free_daily_limit} на чат" in text
    assert f"{settings.group_member_paid_daily_limit}" in text


def test_help_ai_plus_section_documents_subscription_summary_and_autocfg() -> None:
    text, _ = _resolve_help_payload(_settings(), section="ai_plus")
    for fragment in ("/premium", "/summary", "daily_summary_enabled", "/autocfg", "/autocfgcancel", f"{_policy_limit(AiFeature.DAILY_SUMMARY, 'manual')} раз в месяц"):
        assert fragment in text, fragment


def test_help_models_section_lists_profiles_limit_modes_and_grants() -> None:
    settings = _settings()
    text, _ = _resolve_help_payload(settings, section="models")
    for fragment in ("/ai", "Базовая", "Аналитик", "Быстрая", "AI Limits", "AIL"):
        assert fragment in text, fragment
    assert len(text) < 4096


def test_help_pets_and_subscription_sections_cover_custom_actions_and_grants() -> None:
    settings = _settings()
    pets, _ = _resolve_help_payload(settings, section="pets")
    assert "/pet_do" in pets and "/pet_traits" in pets and "/pet_memory" in pets
    group, _ = _resolve_help_payload(settings, section="ai")
    assert f"{settings.pet_custom_actions_daily_limit} в сутки" in group
    plus, _ = _resolve_help_payload(settings, section="ai_plus")
    assert "выдана администратором" in plus
    for text in (pets, group, plus):
        assert len(text) < 4096


def _callback_query(*, data: str, user_id: int, edit_side_effect: Exception | None = None) -> SimpleNamespace:
    message = SimpleNamespace(edit_text=AsyncMock(side_effect=edit_side_effect))
    return SimpleNamespace(
        data=data,
        message=message,
        from_user=SimpleNamespace(id=user_id),
        answer=AsyncMock(),
    )


async def test_help_section_is_readable_by_a_group_member_who_did_not_open_it() -> None:
    # /help is public in groups: another participant pressing a card's button
    # gets the section, not an "other user's menu" refusal.
    query = _callback_query(data="help:economy:u999", user_id=1)

    await help_callback(query, _settings())

    query.message.edit_text.assert_awaited_once()
    text = query.message.edit_text.await_args.args[0]
    assert "Экономика" in text
    query.answer.assert_awaited_once_with()
    assert all("другого пользователя" not in str(call) for call in query.answer.await_args_list)


async def test_help_new_card_keyboard_carries_no_owner_suffix() -> None:
    query = _callback_query(data="help:games", user_id=1)

    await help_callback(query, _settings())

    keyboard = query.message.edit_text.await_args.kwargs["reply_markup"]
    callbacks = [button.callback_data for row in keyboard.inline_keyboard for button in row]
    assert "help:game_mafia" in callbacks
    assert all(":u" not in callback for callback in callbacks)


async def test_help_callback_treats_not_modified_as_success() -> None:
    error = TelegramBadRequest(method=SimpleNamespace(), message="Bad Request: message is not modified")
    query = _callback_query(data="help:economy", user_id=1, edit_side_effect=error)

    await help_callback(query, _settings())

    query.answer.assert_awaited_once_with()


async def test_help_callback_reraises_other_edit_errors() -> None:
    error = TelegramBadRequest(method=SimpleNamespace(), message="Bad Request: message to edit not found")
    query = _callback_query(data="help:economy", user_id=1, edit_side_effect=error)

    with pytest.raises(TelegramBadRequest, match="message to edit not found"):
        await help_callback(query, _settings())


async def test_help_callback_without_message_only_acknowledges() -> None:
    query = SimpleNamespace(data="help:economy", message=None, from_user=SimpleNamespace(id=1), answer=AsyncMock())

    await help_callback(query, _settings())

    query.answer.assert_awaited_once_with()
