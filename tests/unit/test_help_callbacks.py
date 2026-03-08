from selara.core.config import Settings
from selara.presentation.handlers.help import (
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
