"""Regression tests for docs/GAMES_UX_MODERNIZATION_TODO.md Stage 4 (Dice,
first game ported from the Spy-verified pattern): board text explicitly
separates "Сейчас" / "Что делать" from progress, and the progress line now
also names who hasn't rolled yet -- matching Spy's "Ещё без голоса" pattern.
No mechanic change: still one roll per player, highest wins."""
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


def _dice_game(*, dice_scores: dict[int, int] | None = None) -> GroupGame:
    return GroupGame(
        game_id="g1", kind="dice", chat_id=-100, chat_title="chat", owner_user_id=1,
        players={1: "Alice", 2: "Bob", 3: "Cara"}, status="started", phase="freeplay",
        dice_scores=dice_scores or {},
    )


def test_board_text_separates_current_state_from_action() -> None:
    text = game_router._render_game_text(_dice_game(), _chat_settings())
    assert "<b>Сейчас:</b>" in text
    assert "<b>Что делать:</b>" in text


def test_progress_names_who_still_needs_to_roll() -> None:
    text = game_router._render_dice_progress(_dice_game(dice_scores={1: 4}))
    assert "Бросили:</b> 1/3" in text
    assert "Ждём бросок" in text
    assert "Bob" in text
    assert "Cara" in text
    assert "Alice" not in text.split("Ждём бросок")[1]


def test_progress_omits_waiting_line_once_everyone_has_rolled() -> None:
    text = game_router._render_dice_progress(_dice_game(dice_scores={1: 4, 2: 2, 3: 6}))
    assert "Ждём бросок" not in text
