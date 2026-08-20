"""Regression tests for docs/GAMES_UX_MODERNIZATION_TODO.md Stage 5: the
"Ждём: <names>" / "Ещё без голоса: <names>" waiting-list line was a near-
identical 3-line block (compute waiting ids -> skip if empty -> append
"<label>: names") duplicated at 8 call sites across 6 games (Spy, Dice,
Bunker, Quiz, Bredovukha x2, Zlobcards x2, Mafia x2). Extracted to
_append_waiting_line; this is a behavior-preserving refactor -- every
existing game test still passes unchanged, this file covers the helper
itself directly."""
from __future__ import annotations

import importlib

from selara.presentation.game_state import GroupGame

game_router = importlib.import_module("selara.presentation.handlers.game.router")


def _game() -> GroupGame:
    return GroupGame(
        game_id="g1", kind="dice", chat_id=-100, chat_title="chat", owner_user_id=1,
        players={1: "Alice", 2: "Bob", 3: "Cara"},
    )


def test_appends_a_line_naming_who_has_not_answered() -> None:
    lines: list[str] = []
    game_router._append_waiting_line(lines, _game(), pool=_game().players.keys(), answered={1: 4})
    assert len(lines) == 1
    assert "<b>Ждём:</b>" in lines[0]
    assert "Bob" in lines[0] and "Cara" in lines[0]
    assert "Alice" not in lines[0]


def test_appends_nothing_when_everyone_has_answered() -> None:
    lines: list[str] = []
    game = _game()
    game_router._append_waiting_line(lines, game, pool=game.players.keys(), answered=dict.fromkeys(game.players, 0))
    assert lines == []


def test_custom_label_is_used() -> None:
    lines: list[str] = []
    game = _game()
    game_router._append_waiting_line(lines, game, pool=game.players.keys(), answered={}, label="Ждём бросок")
    assert "<b>Ждём бросок:</b>" in lines[0]
