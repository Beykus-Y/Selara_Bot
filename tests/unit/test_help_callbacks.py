from selara.core.config import Settings
from selara.presentation.commands.command_catalog import GAME_RULES_RU
from selara.presentation.game_state import GAME_LAUNCHABLE_KINDS
from selara.presentation.handlers.help import (
    _HELP_GAMES_ORDER,
    _HELP_SECTIONS_ORDER,
    _build_help_keyboard,
    _parse_help_callback_data,
    _resolve_help_payload,
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
    keyboard = _build_help_keyboard(section="games", owner_user_id=None)
    callbacks = [button.callback_data for row in keyboard.inline_keyboard for button in row]
    assert "help:home" in callbacks


def test_help_unknown_section_falls_back_to_main_text() -> None:
    text, keyboard = _resolve_help_payload(_settings(), section="unknown")
    assert "Выберите раздел" in text
    assert keyboard.inline_keyboard


def test_help_games_section_shows_game_picker() -> None:
    text, keyboard = _resolve_help_payload(_settings(), section="games", owner_user_id=77)
    callbacks = [button.callback_data for row in keyboard.inline_keyboard for button in row]
    assert "Выберите конкретную игру" in text
    assert "help:game_mafia:u77" in callbacks
    assert "help:game_spy:u77" in callbacks
    assert "help:game_bunker:u77" in callbacks


def test_help_game_payload_contains_rules() -> None:
    text, keyboard = _resolve_help_payload(_settings(), section="game_quiz", owner_user_id=10)
    callbacks = [button.callback_data for row in keyboard.inline_keyboard for button in row]
    assert "Викторина" in text
    assert "Правила" in text
    assert "help:games:u10" in callbacks
    assert "help:home:u10" in callbacks


def test_help_callback_parser_extracts_owner() -> None:
    section, owner_id = _parse_help_callback_data("help:game_mafia:u123")
    assert section == "game_mafia"
    assert owner_id == 123


def test_help_callback_parser_works_for_legacy_format() -> None:
    section, owner_id = _parse_help_callback_data("help:economy")
    assert section == "economy"
    assert owner_id is None


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
    keyboard = _build_help_keyboard(section="games", owner_user_id=None)
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
