"""Regression test for docs/GAMES_UX_MODERNIZATION_TODO.md Stage 4,
correction #2: numeric lobby steppers (➖ value ➕) must land on one row
together. The old blanket `builder.adjust(2)` (2 buttons per row for the
whole lobby keyboard) split them across row boundaries -- e.g. Zlobcards'
"➕ Раунды" ended up on the same row as "➖ Цель", a real misclick risk
(tapping what looks like "increase rounds, decrease target" as one visual
group). Explicit per-row sizing for the lobby keyboard now keeps each
stepper triple together, without forcing every game onto the same layout
(Spy/WhoAmI/Mafia have no stepper at all and are unaffected)."""
from __future__ import annotations

import importlib

from selara.presentation.game_state import GroupGame

game_router = importlib.import_module("selara.presentation.handlers.game.router")


def _lobby_game(kind: str) -> GroupGame:
    return GroupGame(
        game_id="g1", kind=kind, chat_id=-100, chat_title="chat", owner_user_id=1,
        players={1: "Alice", 2: "Bob", 3: "Cara"}, status="lobby",
    )


def test_zlobcards_both_steppers_each_get_their_own_row() -> None:
    kb = game_router._build_game_controls(game=_lobby_game("zlobcards"), bot_username="selara_test_bot")
    rows = [[b.text for b in row] for row in kb.inline_keyboard]
    assert ["➖ Раунды", "🔢 Раунды: 8", "➕ Раунды"] in rows
    assert ["➖ Цель", "🏁 Цель: 7", "➕ Цель"] in rows
    # No row mixes a rounds-control with a target-control.
    for row in rows:
        assert not ({"➖ Раунды", "➕ Раунды"} & set(row) and {"➖ Цель", "➕ Цель"} & set(row))


def test_bredovukha_stepper_gets_its_own_row() -> None:
    kb = game_router._build_game_controls(game=_lobby_game("bredovukha"), bot_username="selara_test_bot")
    rows = [[b.text for b in row] for row in kb.inline_keyboard]
    assert ["➖ Раунды", "🔢 Раундов: 5", "➕ Раунды"] in rows


def test_bunker_stepper_gets_its_own_row() -> None:
    kb = game_router._build_game_controls(game=_lobby_game("bunker"), bot_username="selara_test_bot")
    rows = [[b.text for b in row] for row in kb.inline_keyboard]
    assert ["➖ Места", "🏚 Мест: 0", "➕ Места"] in rows


def test_games_without_a_stepper_are_unaffected() -> None:
    kb = game_router._build_game_controls(game=_lobby_game("spy"), bot_username="selara_test_bot")
    rows = [[b.text for b in row] for row in kb.inline_keyboard]
    assert ["🗺 Тема: случайная тема"] in rows
    assert ["🎬 Старт", "🛑 Отменить"] in rows
