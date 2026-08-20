"""Regression tests for docs/GAMES_UX_MODERNIZATION_TODO.md Stage 4 (5/7,
Zlobcards): board text splits the blended "Сейчас: <state + action>" lines
into separate "Сейчас" / "Что делать" for both phases, and the now-
redundant action-instruction lines inside the round-status block were
trimmed. No mechanic change (still black card -> private card submission
-> anonymous vote). This game was already closest to the target pattern
(private-hand-as-buttons, edit-in-place submission) -- confirmed unchanged.
Lobby stepper row-packing is covered separately in
test_game_lobby_stepper_rows.py."""
from __future__ import annotations

import importlib

from selara.core.chat_settings import default_chat_settings
from selara.core.config import Settings
from selara.presentation.game_state import GroupGame

game_router = importlib.import_module("selara.presentation.handlers.game.router")


def _chat_settings():
    settings = Settings.model_validate(
        {
            "BOT_TOKEN": "123456:TEST",
            "DATABASE_URL": "postgresql+asyncpg://user:pass@localhost:5432/selara_test",
            "BOT_USERNAME": "selara_test_bot",
            "WEB_AUTH_SECRET": "secret",
        }
    )
    return default_chat_settings(settings)


def _zlob_game(**overrides) -> GroupGame:
    defaults = dict(
        game_id="g1", kind="zlobcards", chat_id=-100, chat_title="chat", owner_user_id=1,
        players={1: "Alice", 2: "Bob", 3: "Cara"}, status="started",
        zlob_rounds=8, zlob_target_score=7,
    )
    defaults.update(overrides)
    return GroupGame(**defaults)


def test_private_answers_phase_separates_state_from_action() -> None:
    game = _zlob_game(phase="private_answers", zlob_black_text="Чёрная карта __", zlob_black_slots=1)
    text = game_router._render_game_text(game, _chat_settings())
    assert "<b>Сейчас:</b> сбор карт в ЛС." in text
    assert "<b>Что делать:</b> выберите карту(ы) из руки" in text


def test_public_vote_phase_separates_state_from_action() -> None:
    game = _zlob_game(phase="public_vote", zlob_black_text="Чёрная карта __", zlob_black_slots=1, zlob_options=("а", "б"))
    text = game_router._render_game_text(game, _chat_settings())
    assert "<b>Сейчас:</b> голосование за лучший анонимный вариант." in text
    assert "<b>Что делать:</b> голосуйте кнопкой" in text


def test_waiting_lists_are_unchanged() -> None:
    game = _zlob_game(
        phase="private_answers", zlob_black_text="Чёрная карта __", zlob_black_slots=1,
        zlob_submissions={1: (0,)},
    )
    text = game_router._render_zlob_round_status(game)
    assert "Сдано:</b> 1/3" in text
    assert "Ждём:" in text
    assert "Bob" in text and "Cara" in text
