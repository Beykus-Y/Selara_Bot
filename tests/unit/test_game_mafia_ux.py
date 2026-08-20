"""Regression tests for docs/GAMES_UX_MODERNIZATION_TODO.md Stage 4 (7/7,
Mafia -- last, since most of its UX was already fixed in Stage 3: night
DM-failure warning, unified button+@mention recovery, "day vote opened"
feed-event noise removed). This stage just applies the same board-text
split to Mafia's remaining 4 phases (night / day_discussion / day_vote /
day_execution_confirm) that every other game already got, plus adds a
missing waiting list for execution-confirm voting (day_vote already had
one). No mechanic change."""
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


def _mafia_game(**overrides) -> GroupGame:
    defaults = dict(
        game_id="g1", kind="mafia", chat_id=-100, chat_title="chat", owner_user_id=1,
        players={1: "Alice", 2: "Bob", 3: "Cara", 4: "Dan"}, status="started",
        alive_player_ids={1, 2, 3, 4},
    )
    defaults.update(overrides)
    return GroupGame(**defaults)


def test_night_phase_separates_state_from_action() -> None:
    text = game_router._render_game_text(_mafia_game(phase="night"), _chat_settings())
    assert "<b>Сейчас:</b> ночь" in text
    assert "<b>Что делать:</b> роли с ночным действием ходят в ЛС" in text


def test_day_discussion_phase_separates_state_from_action() -> None:
    text = game_router._render_game_text(_mafia_game(phase="day_discussion"), _chat_settings())
    assert "<b>Сейчас:</b> обсуждение перед голосованием" in text
    assert "<b>Что делать:</b> сверяйте версии" in text


def test_day_vote_phase_separates_state_from_action() -> None:
    text = game_router._render_game_text(_mafia_game(phase="day_vote"), _chat_settings())
    assert "<b>Сейчас:</b> дневное голосование" in text
    assert "<b>Что делать:</b> выберите кандидата кнопками" in text
    assert "<b>Ждём:</b>" in text  # pre-existing, unchanged


def test_execution_confirm_phase_separates_state_and_now_shows_waiting_list() -> None:
    game = _mafia_game(phase="day_execution_confirm", mafia_execution_candidate_user_id=2)
    text = game_router._render_game_text(game, _chat_settings())
    assert "<b>Сейчас:</b> подтверждение казни" in text
    assert "<b>Что делать:</b> голосуйте за/против казни" in text
    assert "<b>Ждём:</b>" in text
    assert "Alice" in text.split("Ждём:")[-1]
