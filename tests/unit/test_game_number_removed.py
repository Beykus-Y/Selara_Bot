"""Regression test for docs/GAMES_UX_MODERNIZATION_TODO.md's cleanup item:
the orphaned "Number" (Угадай число) game was fully retired from the
catalog in the Stage 4 audit (excluded from GAME_LAUNCHABLE_KINDS, refused
by /game with "больше не доступна") but the implementation, GameKind
literal, GAME_DEFINITIONS entry, and -- critically -- user-facing help
text and command-catalog entries were left in place, meaning /help and
the natural-language command catalog described a game that could never
actually be started. This test pins that the game is fully gone from
every user-facing surface, not just unreachable via the catalog."""
from __future__ import annotations

import importlib


def test_number_is_not_a_valid_game_kind() -> None:
    from selara.presentation.game_state import GAME_DEFINITIONS, GameKind

    assert "number" not in GAME_DEFINITIONS
    assert "number" not in GameKind.__args__  # type: ignore[attr-defined]


def test_number_is_not_in_the_help_games_list() -> None:
    help_module = importlib.import_module("selara.presentation.handlers.help")
    kinds = {kind for kind, _ in help_module._HELP_GAMES_ORDER}
    assert "number" not in kinds


def test_number_has_no_command_catalog_entry() -> None:
    from selara.presentation.commands.command_catalog import GAME_RULES_RU

    assert "number" not in GAME_RULES_RU


def test_number_aliases_are_not_recognized_by_the_game_kind_parser() -> None:
    game_router = importlib.import_module("selara.presentation.handlers.game.router")

    for alias in ("число", "угадай", "угадай число", "number", "num"):
        assert game_router._parse_kind(alias) is None, f"{alias!r} should no longer resolve to a game kind"
